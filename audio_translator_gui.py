import tkinter as tk
from tkinter import filedialog, ttk, scrolledtext
import threading
import os

# Import main operations from translate.py
import translate

class AudioTranslatorGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Audio Translator")
        self.geometry("900x600")
        self.audio_path = None
        self.segments = []
        self.translations = []
        self.language = ""
        self.language_probability = 0.0
        self.create_widgets()

    def create_widgets(self):
        frm = ttk.Frame(self)
        frm.pack(fill=tk.X, padx=10, pady=10)

        self.select_btn = ttk.Button(frm, text="Select Audio File", command=self.select_file)
        self.select_btn.pack(side=tk.LEFT)

        self.run_btn = ttk.Button(frm, text="Transcribe & Translate", command=self.run_transcribe_translate, state=tk.DISABLED)
        self.run_btn.pack(side=tk.LEFT, padx=10)

        self.status_lbl = ttk.Label(frm, text="Status: Waiting for file selection")
        self.status_lbl.pack(side=tk.LEFT, padx=10)

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.orig_tab = ttk.Frame(self.tabs)
        self.trans_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.orig_tab, text="Original Transcript")
        self.tabs.add(self.trans_tab, text="English Translation")

        self.orig_text = scrolledtext.ScrolledText(self.orig_tab, wrap=tk.WORD, font=("Consolas", 11))
        self.orig_text.pack(fill=tk.BOTH, expand=True)

        self.trans_text = scrolledtext.ScrolledText(self.trans_tab, wrap=tk.WORD, font=("Consolas", 11))
        self.trans_text.pack(fill=tk.BOTH, expand=True)

    def select_file(self):
        filetypes = [("Audio files", "*.mp3 *.wav *.m4a *.flac *.ogg *.mp4 *.mkv *.webm"), ("All files", "*.*")]
        path = filedialog.askopenfilename(title="Select Audio File", filetypes=filetypes)
        if path:
            self.audio_path = path
            self.status_lbl.config(text=f"Selected: {os.path.basename(path)}")
            self.run_btn.config(state=tk.NORMAL)
            self.orig_text.delete(1.0, tk.END)
            self.trans_text.delete(1.0, tk.END)

    def run_transcribe_translate(self):
        self.run_btn.config(state=tk.DISABLED)
        self.status_lbl.config(text="Processing...")
        threading.Thread(target=self.process_audio).start()

    def process_audio(self):
        try:
            # Ensure audio_path is not None before processing
            if not self.audio_path:
                self.status_lbl.config(text="No audio file selected.")
                return
            # Use translate.py's transcribe function
            res = translate.transcribe(self.audio_path)
            self.language = res["detected_language"]
            self.language_probability = res["language_probability"]
            self.segments = res["segments"]

            src_mt = "zh" if self.language.startswith("zh") else self.language
            if src_mt not in translate.SUPPORTED:
                self.status_lbl.config(text=f"Unsupported language: {self.language}")
                return

            # Use translate.py's load_mt and translate_chunks_parallel
            tok, mdl = translate.load_mt(src_mt)
            texts = [s["text"] for s in self.segments]
            translations = translate.translate_chunks_parallel(texts, tok, mdl, num_beams=8)
            self.translations = translations
            self.display_results()
            self.status_lbl.config(text=f"Done. Language: {self.language} ({self.language_probability:.2f})")
        except Exception as e:
            self.status_lbl.config(text=f"Error: {str(e)}")

    def display_results(self):
        self.orig_text.delete(1.0, tk.END)
        self.trans_text.delete(1.0, tk.END)
        for seg in self.segments:
            start = seg["start"]
            end = seg["end"]
            self.orig_text.insert(tk.END, f"[{start} - {end}] {seg['text'].strip()}\n")
        for seg, tr in zip(self.segments, self.translations):
            start = seg["start"]
            end = seg["end"]
            self.trans_text.insert(tk.END, f"[{start} - {end}] {tr}\n")

if __name__ == "__main__":
    AudioTranslatorGUI().mainloop()
if __name__ == "__main__":
    AudioTranslatorGUI().mainloop()
