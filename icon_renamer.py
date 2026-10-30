#!/usr/bin/env python3
"""
Icon / Image Batch Renamer using Local LM Studio (or any OpenAI-compatible API)
Supports folder drag-and-drop, preview first 10, then process the rest.
Optional auto-categorization into user-defined category folders.
Supports concurrent API calls and stoppable batch processing.
"""

import base64
import json
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Tuple

import requests

# GUI
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# Optional drag-and-drop
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False
    TkinterDnD = tk
    DND_FILES = None

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
CONFIG_PATH = Path(__file__).resolve().parent / "icon_renamer_settings.json"
UNCATEGORIZED = "uncategorized"
DEFAULT_PROMPT = (
    "This is a small pixel art game icon. "
    "Describe the main object in 2-5 English words, suitable as a filename. "
    "Use lowercase with underscores. Do not include file extension. "
    "Only output the filename words, nothing else. "
    'Name it without "pixel" in the name.'
)
DEFAULT_SETTINGS = {
    "api_url": "http://127.0.0.1:1234/v1",
    "api_key": "lm-studio",
    "model": "qwen3-vl-8b-instruct",
    "prompt": DEFAULT_PROMPT,
    "categories_enabled": True,
    "categories": [],
    "concurrency": 10,
}


def natural_sort_key(path: Path):
    """Sort c1, c2, c10 instead of c1, c10, c100, c2."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]


def sanitize_filename(name: str) -> str:
    """Make a safe filename."""
    name = name.strip().lower()
    cleaned = "".join(c if c.isalnum() or c in " _-" else "_" for c in name)
    cleaned = "_".join(cleaned.split())
    cleaned = cleaned.strip("_")
    return cleaned[:80] or "unnamed"


def sanitize_category(name: str) -> str:
    """Make a safe folder / category name."""
    name = name.strip()
    cleaned = "".join(c if c.isalnum() or c in " _-" else "_" for c in name)
    cleaned = "_".join(cleaned.split())
    cleaned = cleaned.strip("_")
    return cleaned[:60] or UNCATEGORIZED


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    settings["categories"] = list(DEFAULT_SETTINGS["categories"])
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for key in DEFAULT_SETTINGS:
                    if key not in saved:
                        continue
                    if key == "categories" and isinstance(saved[key], list):
                        settings["categories"] = [
                            sanitize_category(str(c)) for c in saved[key] if str(c).strip()
                        ]
                    elif key == "categories_enabled":
                        settings[key] = bool(saved[key])
                    elif key == "concurrency":
                        try:
                            settings[key] = max(1, min(64, int(saved[key])))
                        except Exception:
                            settings[key] = DEFAULT_SETTINGS["concurrency"]
                    else:
                        settings[key] = saved[key]
    except Exception:
        pass
    return settings


class IconRenamerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Icon Batch Renamer (Local LM Studio)")
        self.root.geometry("1040x860")
        self.root.minsize(860, 700)

        self.folder_path: Optional[Path] = None
        self.image_files: List[Path] = []
        self.preview_results = []  # list of (path, suggested_name, category)
        self.processing = False
        self.stop_event = threading.Event()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._settings = load_settings()
        self._saving = False
        self._progress_lock = threading.Lock()
        self._completed_count = 0

        self._build_ui()
        if HAS_DND:
            self._setup_dnd()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def get_categories(self) -> List[str]:
        return list(self.category_listbox.get(0, "end"))

    def categories_enabled(self) -> bool:
        return bool(self.categories_enabled_var.get())

    def active_categories(self) -> List[str]:
        """Categories used for naming/output; empty when feature is off."""
        if not self.categories_enabled():
            return []
        return self.get_categories()

    def get_concurrency(self) -> int:
        try:
            return max(1, min(64, int(self.concurrency_var.get())))
        except Exception:
            return 10

    def save_settings(self, *_args):
        if self._saving:
            return
        self._saving = True
        try:
            data = {
                "api_url": self.url_var.get().strip(),
                "api_key": self.key_var.get(),
                "model": self.model_var.get().strip(),
                "prompt": self.get_prompt(),
                "categories_enabled": self.categories_enabled(),
                "categories": self.get_categories(),
                "concurrency": self.get_concurrency(),
            }
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"Failed to save settings: {e}")
        finally:
            self._saving = False

    def on_close(self):
        self.request_stop()
        self.save_settings()
        self.root.destroy()

    def _build_ui(self):
        # --- Settings Frame ---
        settings = ttk.LabelFrame(self.root, text="API Settings", padding=10)
        settings.pack(fill="x", padx=10, pady=5)

        ttk.Label(settings, text="API URL:").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar(value=self._settings["api_url"])
        ttk.Entry(settings, textvariable=self.url_var, width=50).grid(row=0, column=1, sticky="ew", padx=5)

        ttk.Label(settings, text="API Key:").grid(row=1, column=0, sticky="w", pady=4)
        self.key_var = tk.StringVar(value=self._settings["api_key"])
        ttk.Entry(settings, textvariable=self.key_var, width=50, show="*").grid(row=1, column=1, sticky="ew", padx=5)

        ttk.Label(settings, text="Model:").grid(row=2, column=0, sticky="w")
        self.model_var = tk.StringVar(value=self._settings["model"])
        ttk.Entry(settings, textvariable=self.model_var, width=50).grid(row=2, column=1, sticky="ew", padx=5)

        ttk.Label(settings, text="Concurrency:").grid(row=3, column=0, sticky="w", pady=4)
        self.concurrency_var = tk.StringVar(value=str(self._settings.get("concurrency", 10)))
        concurrency_row = ttk.Frame(settings)
        concurrency_row.grid(row=3, column=1, sticky="w", padx=5)
        self.concurrency_spin = ttk.Spinbox(
            concurrency_row,
            from_=1,
            to=64,
            textvariable=self.concurrency_var,
            width=8,
            command=self.save_settings,
        )
        self.concurrency_spin.pack(side="left")
        self.concurrency_spin.bind("<FocusOut>", self.save_settings)
        ttk.Label(concurrency_row, text="(parallel API calls; default 10; remembered)").pack(
            side="left", padx=(8, 0)
        )

        settings.columnconfigure(1, weight=1)

        # --- Prompt ---
        prompt_frame = ttk.LabelFrame(self.root, text="Prompt (edit anytime, then preview again)", padding=10)
        prompt_frame.pack(fill="x", padx=10, pady=5)

        self.prompt_text = scrolledtext.ScrolledText(prompt_frame, height=3, wrap="word")
        self.prompt_text.insert("1.0", self._settings.get("prompt") or DEFAULT_PROMPT)
        self.prompt_text.pack(fill="x")
        self.prompt_text.bind("<FocusOut>", self.save_settings)

        for var in (self.url_var, self.key_var, self.model_var, self.concurrency_var):
            var.trace_add("write", self.save_settings)

        # --- Categories ---
        cat_frame = ttk.LabelFrame(
            self.root,
            text="Categories (model auto-categorizes; off = rename only)",
            padding=10,
        )
        cat_frame.pack(fill="x", padx=10, pady=5)

        self.categories_enabled_var = tk.BooleanVar(
            value=bool(self._settings.get("categories_enabled", True))
        )
        self.categories_toggle = ttk.Checkbutton(
            cat_frame,
            text="Enable auto-categorization",
            variable=self.categories_enabled_var,
            command=self.on_categories_toggle,
        )
        self.categories_toggle.pack(anchor="w")

        cat_row = ttk.Frame(cat_frame)
        cat_row.pack(fill="x", pady=(6, 0))

        self.category_entry = ttk.Entry(cat_row)
        self.category_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.category_entry.bind("<Return>", lambda _e: self.add_category())

        self.add_category_btn = ttk.Button(cat_row, text="Add", command=self.add_category)
        self.add_category_btn.pack(side="left", padx=2)
        self.remove_category_btn = ttk.Button(cat_row, text="Remove selected", command=self.remove_category)
        self.remove_category_btn.pack(side="left", padx=2)

        list_row = ttk.Frame(cat_frame)
        list_row.pack(fill="x", pady=(6, 0))

        self.category_listbox = tk.Listbox(list_row, height=4, exportselection=False)
        self.category_listbox.pack(side="left", fill="x", expand=True)
        cat_scroll = ttk.Scrollbar(list_row, orient="vertical", command=self.category_listbox.yview)
        cat_scroll.pack(side="right", fill="y")
        self.category_listbox.configure(yscrollcommand=cat_scroll.set)

        for cat in self._settings.get("categories") or []:
            self.category_listbox.insert("end", cat)

        self._update_category_controls()

        # --- Drop / Select ---
        drop_frame = ttk.Frame(self.root)
        drop_frame.pack(fill="x", padx=10, pady=5)

        self.drop_label = ttk.Label(
            drop_frame,
            text="Drop a folder here\nor use the button below",
            relief="ridge",
            padding=20,
            anchor="center",
            background="#f0f4f8",
        )
        self.drop_label.pack(fill="x", pady=5)

        btn_frame = ttk.Frame(drop_frame)
        btn_frame.pack()
        ttk.Button(btn_frame, text="Choose folder", command=self.select_folder).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Preview first 10", command=self.preview_first_10).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Process all", command=self.process_all).pack(side="left", padx=5)
        self.stop_btn = ttk.Button(btn_frame, text="Stop", command=self.request_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=5)

        # --- Progress ---
        progress_frame = ttk.LabelFrame(self.root, text="Progress", padding=8)
        progress_frame.pack(fill="x", padx=10, pady=5)
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_label_var = tk.StringVar(value="0/0")
        style = ttk.Style()
        style.configure("Job.Horizontal.TProgressbar", thickness=18)
        self.progress_bar = ttk.Progressbar(
            progress_frame,
            variable=self.progress_var,
            maximum=100,
            mode="determinate",
            style="Job.Horizontal.TProgressbar",
        )
        self.progress_bar.pack(fill="x", pady=(0, 4))
        self.progress_count_label = ttk.Label(
            progress_frame,
            textvariable=self.progress_label_var,
            anchor="center",
            font=("Menlo", 14, "bold"),
        )
        self.progress_count_label.pack(fill="x")

        # --- Preview Tree ---
        tree_frame = ttk.LabelFrame(self.root, text="Preview results (first 10)", padding=5)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=5)

        cols = ("original", "suggested", "category")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8)
        self.tree.heading("original", text="Original")
        self.tree.heading("suggested", text="Suggested name")
        self.tree.heading("category", text="Category")
        self.tree.column("original", width=220)
        self.tree.column("suggested", width=360)
        self.tree.column("category", width=160)
        self.tree.pack(fill="both", expand=True, side="left")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)

        # --- Log ---
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=5)
        log_frame.pack(fill="both", expand=False, padx=10, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=6, state="disabled")
        self.log_text.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status_var).pack(fill="x", padx=10, pady=2)

    def on_categories_toggle(self):
        self._update_category_controls()
        self.save_settings()

    def _update_category_controls(self):
        state = "normal" if self.categories_enabled() else "disabled"
        self.category_entry.configure(state=state)
        self.add_category_btn.configure(state=state)
        self.remove_category_btn.configure(state=state)
        self.category_listbox.configure(state=state)

    def add_category(self):
        if not self.categories_enabled():
            return
        raw = self.category_entry.get().strip()
        if not raw:
            return
        name = sanitize_category(raw)
        existing = {c.lower() for c in self.get_categories()}
        if name.lower() in existing:
            messagebox.showinfo("Notice", f"Category already exists: {name}")
            return
        self.category_listbox.insert("end", name)
        self.category_entry.delete(0, "end")
        self.save_settings()

    def remove_category(self):
        if not self.categories_enabled():
            return
        selected = list(self.category_listbox.curselection())
        if not selected:
            return
        for idx in reversed(selected):
            self.category_listbox.delete(idx)
        self.save_settings()

    def _setup_dnd(self):
        self.drop_label.drop_target_register(DND_FILES)
        self.drop_label.dnd_bind("<<Drop>>", self.on_drop)

    def on_drop(self, event):
        paths = self.root.tk.splitlist(event.data)
        if paths:
            path = Path(paths[0])
            if path.is_dir():
                self.load_folder(path)
            else:
                self.log("Please drop a folder, not a single file")

    def select_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder with icons")
        if folder:
            self.load_folder(Path(folder))

    def load_folder(self, path: Path):
        self.folder_path = path
        self.image_files = sorted(
            [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
            key=natural_sort_key,
        )
        self.log(f"Loaded folder: {path}")
        self.log(f"Found {len(self.image_files)} images")
        self.status_var.set(f"Loaded {len(self.image_files)} images")
        self.tree.delete(*self.tree.get_children())
        self.preview_results.clear()

    def get_prompt(self) -> str:
        return self.prompt_text.get("1.0", "end").strip()

    def build_model_prompt(self, categories: List[str]) -> str:
        base = self.get_prompt()
        if not categories:
            return base
        cats = ", ".join(categories)
        return (
            f"{base}\n\n"
            "Also choose exactly one category for this icon from this list only: "
            f"[{cats}]. "
            'Respond with JSON only, no markdown: {"name":"filename_words","category":"one_from_list"}'
        )

    def match_category(self, raw: str, categories: List[str]) -> str:
        if not categories:
            return ""
        cleaned = sanitize_category(raw)
        lookup = {c.lower(): c for c in categories}
        if cleaned.lower() in lookup:
            return lookup[cleaned.lower()]
        for cat in categories:
            if cat.lower() in cleaned.lower() or cleaned.lower() in cat.lower():
                return cat
        return UNCATEGORIZED

    def parse_model_response(self, content: str, categories: List[str]) -> Tuple[str, str]:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        category = UNCATEGORIZED if categories else ""

        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                if isinstance(data, dict):
                    name = sanitize_filename(str(data.get("name") or data.get("filename") or ""))
                    if categories:
                        category = self.match_category(str(data.get("category") or ""), categories)
                    return name or "unnamed", category
        except Exception:
            pass

        line = text.split("\n")[0].strip("`\"' ")
        if categories and ("|" in line or "," in line):
            parts = re.split(r"[|,]", line, maxsplit=1)
            name = sanitize_filename(parts[0])
            category = self.match_category(parts[1] if len(parts) > 1 else "", categories)
            return name or "unnamed", category

        return sanitize_filename(line) or "unnamed", category

    def call_model(self, image_path: Path) -> Tuple[str, str]:
        """Call LM Studio / OpenAI-compatible vision API. Returns (name, category)."""
        if self.stop_event.is_set():
            return "stopped", ""

        categories = self.active_categories()
        url = self.url_var.get().rstrip("/")
        if not url.endswith("/chat/completions"):
            url = url + "/chat/completions"

        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
        except Exception as e:
            return f"error_read_{e}", UNCATEGORIZED if categories else ""

        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        max_tokens = 80 if categories else 40

        payload = {
            "model": self.model_var.get(),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.build_model_prompt(categories)},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.key_var.get()}",
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            return self.parse_model_response(content, categories)
        except Exception as e:
            return f"error_{type(e).__name__}", UNCATEGORIZED if categories else ""

    def set_busy(self, busy: bool):
        self.processing = busy
        self.stop_btn.configure(state="normal" if busy else "disabled")

    def request_stop(self):
        if not self.processing:
            return
        self.stop_event.set()
        self.status_var.set("Stopping... (queued tasks canceled; in-flight requests will finish)")
        self.log("Stop requested")
        executor = self._executor
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def preview_first_10(self):
        if not self.image_files:
            messagebox.showwarning("Notice", "Choose or drop a folder first")
            return
        if self.processing:
            messagebox.showinfo("Notice", "Already processing — stop or wait")
            return

        self.stop_event.clear()
        self.set_busy(True)
        workers = self.get_concurrency()
        self.status_var.set(f"Previewing first 10 (concurrency {workers})...")
        self.tree.delete(*self.tree.get_children())
        self.preview_results.clear()
        self.set_progress(0, 10)

        def worker():
            to_process = self.image_files[:10]
            total = len(to_process)
            completed = 0
            executor = ThreadPoolExecutor(max_workers=workers)
            self._executor = executor
            try:
                futures = {
                    executor.submit(self.call_model, img): img
                    for img in to_process
                }
                for future in as_completed(futures):
                    if self.stop_event.is_set():
                        break
                    img = futures[future]
                    try:
                        name, category = future.result()
                    except Exception as e:
                        name, category = f"error_{type(e).__name__}", ""
                    if name == "stopped":
                        continue
                    self.preview_results.append((img, name, category))
                    completed += 1
                    self.root.after(
                        0,
                        lambda p=img, n=name, c=category, done=completed, t=total: (
                            self.tree.insert("", "end", values=(p.name, n, c or "-")),
                            self.set_progress(done, t),
                            self.status_var.set(f"Previewing {done}/{t} ..."),
                        ),
                    )
            finally:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
                self._executor = None
                stopped = self.stop_event.is_set()
                self.root.after(
                    0,
                    lambda: self.status_var.set(
                        "Preview stopped" if stopped else "Preview done — tweak Prompt / Categories and preview again"
                    ),
                )
                self.root.after(0, lambda: self.set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def resolve_output_dir(self) -> Path:
        """Prefer folder/renamed_output; if not writable, use sibling folder_renamed_output."""
        primary = self.folder_path / "renamed_output"
        fallback = self.folder_path.parent / f"{self.folder_path.name}_renamed_output"
        for candidate in (primary, fallback):
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                probe = candidate / ".write_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                return candidate
            except Exception:
                continue
        raise PermissionError(
            f"Could not create a writable output folder.\nTried:\n{primary}\n{fallback}"
        )

    def _save_one_result(
        self,
        img: Path,
        suggested: str,
        category: str,
        categories: List[str],
        output_dir: Path,
    ) -> dict:
        if categories:
            cat_folder = sanitize_category(category or UNCATEGORIZED)
            target_dir = output_dir / cat_folder
        else:
            cat_folder = ""
            target_dir = output_dir

        target_dir.mkdir(parents=True, exist_ok=True)

        new_name = suggested + img.suffix.lower()
        new_path = target_dir / new_name
        counter = 1
        while new_path.exists():
            new_name = f"{suggested}_{counter}{img.suffix.lower()}"
            new_path = target_dir / new_name
            counter += 1

        rel_output = f"{cat_folder}/{new_name}" if cat_folder else new_name
        shutil.copy2(img, new_path)
        return {
            "original": img.name,
            "suggested": suggested,
            "category": cat_folder or None,
            "output": rel_output,
        }

    def process_all(self):
        if not self.image_files:
            messagebox.showwarning("Notice", "Choose a folder first")
            return
        if self.processing:
            messagebox.showinfo("Notice", "Already processing — stop or wait")
            return

        categories = self.active_categories()
        try:
            output_dir = self.resolve_output_dir()
        except Exception as e:
            messagebox.showerror("Cannot create output folder", str(e))
            return

        workers = self.get_concurrency()
        if self.categories_enabled() and categories:
            cat_note = f"and sort into category subfolders ({len(categories)} categories)"
        elif self.categories_enabled():
            cat_note = "(categorization on, but no categories yet — all files go in the output folder)"
        else:
            cat_note = "(auto-categorization off — all files go in the output folder)"
        if not messagebox.askyesno(
            "Confirm",
            f"Process all {len(self.image_files)} images?\n"
            f"Concurrency: {workers}\n"
            f"Originals stay untouched. Output goes to:\n{output_dir}\n"
            f"{cat_note}\n"
            f"Preview first if you want to check quality.",
        ):
            return

        mapping_path = output_dir / "rename_mapping.json"
        total = len(self.image_files)

        self.stop_event.clear()
        self.set_busy(True)
        self._completed_count = 0
        self.set_progress(0, total)
        self.status_var.set(f"Starting batch (concurrency {workers})...")
        self.log(f"Output folder: {output_dir}")
        self.log(f"Concurrency: {workers}")
        if categories:
            self.log(f"Categories: {', '.join(categories)}")

        def worker():
            results = []
            results_lock = threading.Lock()
            executor = ThreadPoolExecutor(max_workers=workers)
            self._executor = executor
            try:
                futures = {
                    executor.submit(self.call_model, img): img
                    for img in self.image_files
                }
                for future in as_completed(futures):
                    if self.stop_event.is_set():
                        break
                    img = futures[future]
                    try:
                        suggested, category = future.result()
                    except Exception as e:
                        suggested, category = f"error_{type(e).__name__}", (
                            UNCATEGORIZED if categories else ""
                        )

                    if suggested == "stopped":
                        continue

                    try:
                        item = self._save_one_result(
                            img, suggested, category, categories, output_dir
                        )
                        with results_lock:
                            results.append(item)
                        self.root.after(
                            0,
                            lambda o=item["original"], n=item["output"]: self.log(f"{o} → {n}"),
                        )
                    except Exception as e:
                        self.root.after(
                            0,
                            lambda o=img.name, e=e: self.log(f"Failed to write {o}: {e}"),
                        )

                    with self._progress_lock:
                        self._completed_count += 1
                        done = self._completed_count
                    self.root.after(
                        0,
                        lambda d=done, t=total: (
                            self.set_progress(d, t),
                            self.status_var.set(f"Processing {d}/{t} ..."),
                        ),
                    )
            except Exception as e:
                self.root.after(0, lambda: self.log(f"Batch interrupted: {e}"))
                self.root.after(0, lambda: messagebox.showerror("Processing failed", str(e)))
            finally:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
                self._executor = None
                stopped = self.stop_event.is_set()
                try:
                    with open(mapping_path, "w", encoding="utf-8") as f:
                        json.dump(results, f, ensure_ascii=False, indent=2)
                    self.root.after(0, lambda: self.log(f"Mapping saved: {mapping_path}"))
                except Exception as e:
                    self.root.after(0, lambda: self.log(f"Failed to save mapping: {e}"))

                if stopped:
                    msg = f"Stopped ({len(results)}/{total} done) → {output_dir}"
                else:
                    msg = f"All done → {output_dir}"
                self.root.after(0, lambda: self.status_var.set(msg))
                self.root.after(0, lambda: self.set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def set_progress(self, current: int, total: int):
        total = max(total, 0)
        current = max(0, min(current, total if total else 0))
        percent = (current / total * 100) if total else 0
        self.progress_var.set(percent)
        self.progress_label_var.set(f"{current}/{total}")

    def log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def main():
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
        print("Note: tkinterdnd2 not installed; drag-and-drop disabled. Run: pip install tkinterdnd2")

    app = IconRenamerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
