# -*- coding: utf-8 -*-
import sys
import PyQt5
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *

from pathlib import Path
from PyQt5.QtCore import pyqtSlot

# ----------------------------------
import requests
import tempfile
from bs4 import BeautifulSoup
from urllib.parse import urlparse, ParseResult, urljoin
import shutil
import re
import pickle
import datetime

def download_website(original_url, dir, running, resume, filter_fn, logfn=print):
    dir = Path(dir)
    dir.mkdir(exist_ok=True)
    session_dir = dir / 'sessions'
    session_dir.mkdir(exist_ok=True)

    try:
        parsed = urlparse(original_url)
        original_netloc = parsed.netloc
        def is_netloc_allowed(netloc):
            return original_netloc == netloc or ('www.' + netloc) == netloc
                
        logfn(f'\nDownloading from website: {original_netloc}')
        
        netloc_fn = get_valid_filename(original_netloc)
        to_download_path = session_dir / f'{netloc_fn}.to_download.pkl'
        downloaded_path = session_dir / f'{netloc_fn}.downloaded.pkl'

        if resume:
            logfn('Attempting to resume from previous session')
            try:
                with open(to_download_path, 'rb') as f:
                    to_download = pickle.load(f)
                with open(downloaded_path, 'rb') as f:
                    downloaded = pickle.load(f) 
                logfn(f'Session loaded. Visited links: {len(downloaded)}, links to visit: {len(to_download)}')  
            except Exception as e:
                logfn('Unable to load files, starting anew. Error is:')
                logfn(f'{e}')
                to_download = {original_url}
                downloaded = set()
        else:
            logfn('Resume=False. Starting anew')
            to_download = {original_url}
            downloaded = set()

        while to_download and running():
            url = to_download.pop()
            logfn(f'\n{url}')
            logfn(f'{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
            if url in downloaded:
                logfn(f'Already downloaded: {url}')
                continue
            if not filter_fn(url):
                logfn(f'URL removed by filter: {url}')
                continue
            downloaded.add(url)

            try:
                logfn(f'Sending head request.')
                r = requests.head(url, allow_redirects=True)
                dst = destination_path(r, is_netloc_allowed, logfn)
                if not dst: 
                    # either already downloaded or not text file
                    logfn(f'Skipping {url}')
                    continue 

                dst = dir / str(dst).lstrip('/')
                
                logfn(f'Sending get request.')
                r = requests.get(url, allow_redirects=True)
                if r.status_code != 200:
                    logfn(f'Error while downloading page, status_code {r.status_code}')
                    continue
                
                logfn('Creating parent directory')
                safe_create_parent_directory(dst, logfn)

                logfn(f'Writing file content to {str(dst)}')
                with dst.open('wb') as f_dst:
                    f_dst.write(r.content)
                
                logfn(f'Extracting links')
                links = extract_all_link(r, is_netloc_allowed, logfn)
                if links:
                    links = [l for l in links if l not in downloaded and filter_fn(l)]
                    to_download.update(links)
            finally:
                pass
    finally:
        with open(to_download_path, 'wb') as f:
            pickle.dump(to_download, f)
        with open(downloaded_path, 'wb') as f:
            pickle.dump(downloaded, f)
        


def destination_path(r, is_netloc_allowed, logfn=print):
    parsed_url = urlparse(r.url)
    netloc = parsed_url.netloc
    
    if not is_netloc_allowed(netloc):
        logfn(f'Domain not allowed {netloc}')
        return None

    if r.status_code != 200:
        logfn(f'Error while accessing page (status_code = {r.status_code})')
        return None

    content_type = r.headers['content-type']
    file_extension = ''

    if 'text/html' in content_type:
        file_extension = '.html'
    elif 'text/plain' in content_type:
        file_extension = '.txt'
    elif 'application/json' in content_type:
        file_extension = '.json'
    else:
        logfn(f'Content-type not allowed: {content_type}')
        return None

    # strip fragment
    new_url = remove_fragment(parsed_url).geturl()
    
    # strip_scheme
    schemes = ['//', 'http://', 'https://']
    for scheme in schemes:
        if new_url.startswith(scheme):
            new_url = new_url.replace(scheme, '', 1)

    # add extension
    new_url = new_url.rstrip('/') + file_extension

    # keep only chars allowed in filenames
    return get_valid_filename(new_url)


def remove_fragment(parsed_url):
    return ParseResult(*parsed_url[:-1], '')


def get_valid_filename(s):
    s = str(s).strip().replace(' ', '_')
    # note: I allowed the characters '/' and '#', usually they are not.
    # if you have issue with filenames, check for those!
    return re.sub(r'(?u)[^-\w./#]', '', s)

    
def safe_create_parent_directory(path, root, logfn=print):
    try:
        path = Path(path)
        path.parent.mkdir(exist_ok=True, parents=True)
    except FileExistsError:
        # one of the parents is a file
        for parent in path.parents:
            if parent == root:
                break
            if parent.is_file():
                convert_file_to_directory(parent, logfn)
    finally:
        path.parent.mkdir(exist_ok=True, parents=True)


def convert_file_to_directory(filepath, filename='index.html', logfn=print):
    logfn(f'Converting {filepath} to a directory')
    filepath = Path(filepath)
    filename = str(filename).lstrip('/')
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_file = Path(tmp_dir.name) / filename
        filepath.replace(tmp_file)
        filepath.mkdir(exist_ok=True, parents=True)
        tmp_file.replace(filepath / filename)


def extract_all_link(r, is_netloc_allowed, logfn):
    if 'text/html' not in r.headers['content-type']:
        logfn('Not an html file, unable to find links.')
        return []

    soup = BeautifulSoup(r.content)
    links = set()

    for a in soup.find_all('a'):
        href = a.get('href', None)
        if href:
            url = urljoin(r.url, a['href'])
            parsed = urlparse(url)
            if is_netloc_allowed(parsed.netloc):
                url = remove_fragment(parsed).geturl()
                links.add(url)
    logfn(f'Found {len(links)} links')
    return links


# ----------------------------------


def default_download_dir():
    docs = PyQt5.QtCore.QStandardPaths.writableLocation(PyQt5.QtCore.QStandardPaths.DocumentsLocation)
    docs_path = Path(docs)
    dir = docs_path.joinpath('websites')
    return str(dir)


class Worker(QThread):
    finished = pyqtSignal()
    logger = pyqtSignal(str)
    interrupt = pyqtSignal()

    def __init__(self, url, target_dir, resume, filter_fn, parent = None):
        QThread.__init__(self, parent)
        self.url = url
        self.target_dir = target_dir
        self.resume = resume
        self.filter_fn = filter_fn
        self.interrupt.connect(self.stop_running)

    @pyqtSlot()
    def stop_running(self):
        self.running = False

    def run(self):
        self.running = True
        logfn = self.logger.emit
        try:
            download_website(self.url, self.target_dir, lambda: self.running, self.resume, self.filter_fn, logfn)
            logfn('Terminated.')
        except Exception as e:
            logfn(f'\nFatal error:')
            logfn(f'{str(e)}')
        finally:
            self.finished.emit()

def main():
    app = QApplication([])
    window = QWidget()
    window.setWindowTitle('Web crawler')
    window.setMinimumWidth(500)    
    layout = QVBoxLayout()

    descr_text = QLabel()
    descr_text.setWordWrap(True)
    descr_text.setText(
    "Information: this program downloads HTML and text files from a website.\n"
    "The program first downloads the URL specified below, then follows all links recursively.\n"
    )
    layout.addWidget(descr_text)

    layout.addWidget(QLabel('URL of website to download:'))
    url = QLineEdit()
    url.setPlaceholderText('http://julienharbulot.com')
    layout.addWidget(url)

    layout.addWidget(QLabel('Only download url containing this text: (leave empty to download every url)'))
    text_filter = QLineEdit()
    text_filter.setPlaceholderText('/en/')
    layout.addWidget(text_filter)

    layout.addWidget(QLabel('Directory where to save downloaded files:'))
    dir_row = QHBoxLayout()
    folder = QLineEdit()
    folder.setText(default_download_dir())
    dir_row.addWidget(folder)
    
    @pyqtSlot()
    def on_click():
        path = QFileDialog.getExistingDirectory(window, "Select Directory")
        if path:
            path = str(path)
            folder.setText(path)

    browse_btn = QPushButton('browse')
    browse_btn.clicked.connect(on_click)
    dir_row.addWidget(browse_btn)
    layout.addLayout(dir_row)

    go_btn = QPushButton('Download')
    cancel_btn = QPushButton('Cancel')
    cancel_btn.setDisabled(True)
    output_text = QPlainTextEdit()
    global logfn
    logfn = output_text.appendPlainText
    worker = None

    resume_checkbox = QCheckBox()
    resume_checkbox.setChecked(True)
    resume_label = QLabel('Resume previous session')
    resume_layout = QHBoxLayout()
    resume_layout.addWidget(resume_label)
    resume_layout.addWidget(resume_checkbox)
    resume_checkbox.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
    resume_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
    resume_layout.addStretch(0)
    resume_layout.addSpacing(0)

    def set_ui_enabled(enabled):
        url.setEnabled(enabled)
        folder.setEnabled(enabled)
        browse_btn.setEnabled(enabled)
        go_btn.setEnabled(enabled)
        cancel_btn.setDisabled(enabled)
        text_filter.setEnabled(enabled)
        resume_checkbox.setEnabled(enabled)

    @pyqtSlot()
    def on_output(output):
        output_text.appendPlainText(output)
        output_text.ensureCursorVisible()
    
    @pyqtSlot()
    def on_cancel():
        go_btn.setDisabled(False)
        cancel_btn.setDisabled(True)
        on_output('Canceling tasks, please wait...')
        global worker
        worker.interrupt.emit()
        # Note: worker.finished will re-enable UI

    @pyqtSlot()
    def on_download():
        if not url.text().strip():
            on_output('Please provide an url to download')
            return
        if not folder.text().strip():
            on_output('Please enter path where to download files')
            return
        
        set_ui_enabled(False)
        print('Download button clicked: url=', url.text(), 'target_dir=', folder.text())
        
        url_text = url.text()
        if urlparse(url.text()).scheme == '':
            url_text = 'http://' + url_text
        
        filter_fn = lambda url: text_filter.text() in str(url)

        global worker
        worker = Worker(url.text(), folder.text(), resume_checkbox.isChecked(), filter_fn, parent=window)
        worker.logger.connect(on_output)
        worker.finished.connect(lambda: set_ui_enabled(True))
        worker.start()
 
    cancel_btn.clicked.connect(on_cancel)
    go_btn.clicked.connect(on_download)
    
    spacer = QLabel()
    actions_layout = QHBoxLayout()
    actions_layout.addWidget(go_btn)
    actions_layout.addWidget(cancel_btn)
    actions_layout.addLayout(resume_layout)
    layout.addLayout(actions_layout)
    layout.addWidget(output_text)

    window.setLayout(layout)
    window.show()
    return app.exec_()

if __name__ == '__main__':
    #download_website('http://julienharbulot.com', './websites', lambda:True)
    sys.exit(main())