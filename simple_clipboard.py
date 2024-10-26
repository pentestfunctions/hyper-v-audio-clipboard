import socket
import pyperclip  # pip install pyperclip
import os

def send_clipboard_content(conn):
    # Check if the clipboard contains text
    clipboard_data = pyperclip.paste()  # Retrieve clipboard content
    if clipboard_data:
        conn.sendall(f'TEXT\n{clipboard_data}'.encode('utf-8'))
    else:
        conn.sendall(b'NO CONTENT')

def start_server(host='0.0.0.0', port=65432):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, port))
        s.listen()
        print(f"Listening on {host}:{port}...")
        conn, addr = s.accept()
        with conn:
            print(f"Connected by {addr}")
            send_clipboard_content(conn)

if __name__ == "__main__":
    start_server()
