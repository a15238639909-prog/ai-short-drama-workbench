"""A native folder picker, opened only by the user's button click."""
import json
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
folder = filedialog.askdirectory(parent=root, title='选择抖音视频保存文件夹', initialdir=sys.argv[1] if len(sys.argv) > 1 else None)
root.destroy()
sys.stdout.buffer.write(json.dumps(folder, ensure_ascii=False).encode('utf-8'))
