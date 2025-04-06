#!/usr/bin/env python3

# content-select-downloader  Copyright (C) 2019  Samuel Bachmann <samuel.bachmann@gmail.com>
# This program comes with ABSOLUTELY NO WARRANTY.
# This is free software, and you are welcome to redistribute it.



import argparse
import os
import re
import requests
import tkinter as tk
from tkinter import filedialog, ttk
from bs4 import BeautifulSoup
import fitz  # PyMuPDF
import threading
import queue
import time

class DownloadTask:
    def __init__(self, url, output):
        self.url = url
        self.output = output
        self.status = "Ausstehend"  # Stati: "Ausstehend", "In Bearbeitung", "Abgeschlossen", "Fehler"
        self.message = ""
    
    def update_status(self, status, message=""):
        self.status = status
        self.message = message

class ContentSelectDownloader:
    def __init__(self, url, output, task=None, status_callback=None):
        self.url = url
        # Leerzeichen nicht mehr ersetzen, aber Windows-unzulässige Zeichen bereinigen
        self.output = self.sanitize_filename(output)
        if not self.output.endswith(".pdf"):
            self.output += ".pdf"
        self.session = requests.Session()
        self.task = task
        self.status_callback = status_callback

    def sanitize_filename(self, filename):
        # Zeichen entfernen, die in Windows-Dateinamen nicht erlaubt sind
        # (< > : " / \ | ? * und Steuerzeichen)
        return re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', filename)

    def log(self, message):
        print(message)
        if self.status_callback:
            self.status_callback(message)

    def get_book_urls(self):
        self.log("Suche nach Buch-URLs...")
        response = self.session.get(self.url)
        if response.status_code != 200:
            raise ConnectionError("Fehler beim Abrufen der Webseite.")
        
        soup = BeautifulSoup(response.text, "lxml")
        book_links = [item["href"] for item in soup.select(".book-item a")]
        full_links = [requests.compat.urljoin(self.url, link) for link in book_links]
        
        if not full_links:
            # Möglicherweise ist dies bereits eine direkte Buch-URL
            full_links = [self.url]
        
        return full_links

    def get_pdf_id(self):
        self.log("Extrahiere PDF-ID...")
        result = re.search(r"moz_viewer/([a-z0-9\-]*)/", self.url)
        if result:
            return result.group(1)
        else:
            raise ValueError("Konnte keine PDF-ID aus der URL extrahieren.")

    def get_chapter_ids(self):
        self.log("Extrahiere Kapitel-IDs...")
        response = self.session.get(self.url)
        if response.status_code != 200:
            raise ConnectionError("Fehler beim Abrufen der Webseite.")
        
        soup = BeautifulSoup(response.text, "lxml")
        chapters = [item["data-chapter-id"] for item in soup.select("#printList a")]
        
        if not chapters:
            for item in soup.select("div.outlineItem a"):
                match = re.search(r"#chapter=([a-z0-9]*)", item["href"])
                if match:
                    chapters.append(match.group(1))
        
        if not chapters:
            raise ValueError("Konnte keine Kapitel-IDs finden.")
        
        return chapters

    def download_pdfs(self, pdf_id, chapter_ids):
        self.log("Lade PDFs herunter...")
        files = []
        
        for idx, chapter_id in enumerate(chapter_ids, start=1):
            file_name = f"tmp_{idx}_{pdf_id}.pdf"
            pdf_url = f"https://content-select.com/media/display/{pdf_id}/{chapter_id}"
            
            self.log(f"Lade Kapitel {idx}/{len(chapter_ids)}: {chapter_id}")
            
            response = self.session.get(pdf_url)
            
            if response.status_code == 200 and response.headers.get("Content-Type") == "application/pdf":
                with open(file_name, "wb") as f:
                    f.write(response.content)
                if self.is_valid_pdf(file_name):
                    files.append(file_name)
                else:
                    self.log(f"Warnung: {file_name} ist keine gültige PDF-Datei.")
            else:
                self.log(f"Fehler beim Herunterladen von {pdf_url}: Status {response.status_code}")
            
        return files

    def is_valid_pdf(self, file_path):
        try:
            doc = fitz.open(file_path)
            page_count = len(doc)
            doc.close()
            return page_count > 0
        except Exception as e:
            self.log(f"Fehler beim Validieren von {file_path}: {e}")
            return False
    
    def merge_pdfs(self, files):
        self.log(f"Mische {len(files)} PDFs mit PyMuPDF...")
        merged_document = fitz.open()
        
        for file in files:
            if not self.is_valid_pdf(file):
                self.log(f"Überspringe ungültige Datei: {file}")
                continue
            try:
                with fitz.open(file) as doc:
                    merged_document.insert_pdf(doc)
                    self.log(f"Hinzugefügt: {file} ({len(doc)} Seiten)")
            except Exception as e:
                self.log(f"Fehler beim Hinzufügen von {file}: {e}")
                continue
        
        if len(merged_document) > 0:
            merged_document.save(self.output)
            self.log(f"Erfolgreich erstellt: {self.output} mit {len(merged_document)} Seiten")
            return True
        else:
            self.log("Fehler: Keine Seiten zum Speichern.")
            return False
    
    def clean_up(self, files):
        for file in files:
            if os.path.exists(file):
                try:
                    os.remove(file)
                except Exception as e:
                    self.log(f"Konnte {file} nicht löschen: {e}")
        self.log("Aufräumen abgeschlossen.")

    def run(self):
        if self.task:
            self.task.update_status("In Bearbeitung")
        
        try:
            book_urls = self.get_book_urls()
            for book_url in book_urls:
                self.url = book_url
                pdf_id = self.get_pdf_id()
                chapter_ids = self.get_chapter_ids()
                files = self.download_pdfs(pdf_id, chapter_ids)
                
                if files:
                    success = self.merge_pdfs(files)
                    self.clean_up(files)
                    
                    if success and self.task:
                        self.task.update_status("Abgeschlossen", f"Gespeichert als {self.output}")
                else:
                    if self.task:
                        self.task.update_status("Fehler", "Keine PDF-Dateien gefunden")
                    
        except Exception as e:
            error_msg = f"Fehler: {str(e)}"
            self.log(error_msg)
            if self.task:
                self.task.update_status("Fehler", error_msg)

class DownloadQueueManager:
    def __init__(self, update_ui_callback):
        self.queue = queue.Queue()
        self.tasks = []
        self.running = False
        self.worker_thread = None
        self.update_ui = update_ui_callback
    
    def add_task(self, url, output):
        task = DownloadTask(url, output)
        self.tasks.append(task)
        self.queue.put(task)
        self.update_ui()
        
        if not self.running:
            self.start_worker()
        
        return task
    
    def start_worker(self):
        if not self.running:
            self.running = True
            self.worker_thread = threading.Thread(target=self.process_queue, daemon=True)
            self.worker_thread.start()
    
    def process_queue(self):
        while self.running:
            try:
                task = self.queue.get(block=True, timeout=1)
                self.update_ui()
                
                # Status-Updates an die UI senden
                def status_callback(message):
                    # Wir brauchen ein UI-Update in einem Thread-sicheren Weg
                    if self.update_ui:
                        tk_root.after(10, self.update_ui)
                
                downloader = ContentSelectDownloader(
                    task.url, 
                    task.output, 
                    task=task,
                    status_callback=status_callback
                )
                downloader.run()
                
                self.queue.task_done()
                self.update_ui()
                
            except queue.Empty:
                if self.queue.empty():
                    time.sleep(0.5)  # Kurze Pause, um CPU-Last zu reduzieren
            except Exception as e:
                print(f"Fehler im Worker-Thread: {e}")
        
        print("Worker beendet")
    
    def stop(self):
        self.running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2)
    
    def remove_task(self, task_index):
        if 0 <= task_index < len(self.tasks):
            task = self.tasks.pop(task_index)
            # Wir können nur ausstehende Aufgaben aus der Queue entfernen
            if task.status == "Ausstehend":
                # Wir müssen eine neue Queue erstellen und alle anderen Tasks kopieren
                new_queue = queue.Queue()
                try:
                    while True:
                        queued_task = self.queue.get_nowait()
                        if queued_task != task:
                            new_queue.put(queued_task)
                        self.queue.task_done()
                except queue.Empty:
                    pass
                
                self.queue = new_queue
            
            self.update_ui()
            return True
        return False

def create_gui():
    global tk_root, url_entry, output_entry, queue_listbox, status_label
    
    tk_root = tk.Tk()
    tk_root.title("Content Select Downloader mit Warteschlange")
    tk_root.geometry("800x600")
    
    # Oberer Frame für Eingaben
    input_frame = ttk.Frame(tk_root, padding=10)
    input_frame.pack(fill=tk.X)
    
    ttk.Label(input_frame, text="URL eingeben:").grid(row=0, column=0, sticky=tk.W, pady=5)
    url_entry = ttk.Entry(input_frame, width=70)
    url_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=5)
    
    ttk.Label(input_frame, text="Name der PDF-Datei:").grid(row=1, column=0, sticky=tk.W, pady=5)
    output_entry = ttk.Entry(input_frame, width=70)
    output_entry.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=5)
    
    button_frame = ttk.Frame(input_frame)
    button_frame.grid(row=2, column=0, columnspan=2, pady=10)
    
    ttk.Button(button_frame, text="Zur Warteschlange hinzufügen", command=add_to_queue).pack(side=tk.LEFT, padx=5)
    ttk.Button(button_frame, text="Eingaben löschen", command=clear_entries).pack(side=tk.LEFT, padx=5)
    
    # Mittlerer Frame für die Warteschlange
    queue_frame = ttk.LabelFrame(tk_root, text="Download-Warteschlange", padding=10)
    queue_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    
    # Scrollbare Listbox für die Warteschlange
    scrollbar = ttk.Scrollbar(queue_frame)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    
    queue_listbox = tk.Listbox(queue_frame, width=80, height=15, yscrollcommand=scrollbar.set)
    queue_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.config(command=queue_listbox.yview)
    
    # Buttons für die Warteschlange
    queue_button_frame = ttk.Frame(tk_root, padding=5)
    queue_button_frame.pack(fill=tk.X, padx=10)
    
    ttk.Button(queue_button_frame, text="Ausgewählten Eintrag entfernen", 
               command=remove_selected_task).pack(side=tk.LEFT, padx=5)
    
    # Status-Label
    status_label = ttk.Label(tk_root, text="Bereit", anchor=tk.W, padding=5)
    status_label.pack(fill=tk.X, padx=10, pady=5)
    
    # Fensterschließereignis abfangen
    tk_root.protocol("WM_DELETE_WINDOW", on_closing)
    
    # Queue Manager initialisieren
    global queue_manager
    queue_manager = DownloadQueueManager(update_queue_display)
    
    return tk_root

def update_queue_display():
    queue_listbox.delete(0, tk.END)
    for idx, task in enumerate(queue_manager.tasks):
        status_icon = "⏳" if task.status == "Ausstehend" else "▶️" if task.status == "In Bearbeitung" else "✅" if task.status == "Abgeschlossen" else "❌"
        queue_listbox.insert(tk.END, f"{status_icon} {task.output}: {task.status} {task.message}")
        
        # Farbe je nach Status
        if task.status == "Abgeschlossen":
            queue_listbox.itemconfig(idx, {'fg': 'green'})
        elif task.status == "Fehler":
            queue_listbox.itemconfig(idx, {'fg': 'red'})
        elif task.status == "In Bearbeitung":
            queue_listbox.itemconfig(idx, {'fg': 'blue'})
    
    # Status aktualisieren
    active_tasks = sum(1 for task in queue_manager.tasks if task.status == "In Bearbeitung")
    pending_tasks = sum(1 for task in queue_manager.tasks if task.status == "Ausstehend")
    completed_tasks = sum(1 for task in queue_manager.tasks if task.status == "Abgeschlossen")
    failed_tasks = sum(1 for task in queue_manager.tasks if task.status == "Fehler")
    
    status_label.config(text=f"Status: {active_tasks} aktiv, {pending_tasks} ausstehend, {completed_tasks} abgeschlossen, {failed_tasks} fehlgeschlagen")

def add_to_queue():
    url = url_entry.get().strip()
    output = output_entry.get().strip()
    
    if not url:
        status_label.config(text="Fehler: Bitte gib eine URL ein.")
        return
    
    if not output:
        # Versuche, einen Dateinamen aus der URL zu extrahieren
        try:
            output = re.search(r"/([^/]+)/?$", url).group(1).replace("-", "_")
        except:
            output = f"download_{len(queue_manager.tasks) + 1}"
    
    # Aufgabe zur Warteschlange hinzufügen
    queue_manager.add_task(url, output)
    
    # Eingabefelder leeren
    clear_entries()
    
    status_label.config(text=f"Aufgabe zur Warteschlange hinzugefügt: {output}")

def clear_entries():
    url_entry.delete(0, tk.END)
    output_entry.delete(0, tk.END)

def remove_selected_task():
    selected_indices = queue_listbox.curselection()
    if selected_indices:
        idx = selected_indices[0]
        if queue_manager.remove_task(idx):
            status_label.config(text=f"Aufgabe {idx+1} aus der Warteschlange entfernt")
        else:
            status_label.config(text=f"Konnte Aufgabe {idx+1} nicht entfernen (möglicherweise bereits in Bearbeitung)")
    else:
        status_label.config(text="Bitte wähle eine Aufgabe aus der Liste aus.")

def on_closing():
    if queue_manager:
        queue_manager.stop()
    tk_root.destroy()

if __name__ == '__main__':
    tk_root = create_gui()
    tk_root.mainloop()
