import socket
import pyperclip  # Ensure you have this installed: pip install pyperclip

def send_clipboard_content(conn):
    clipboard_data = pyperclip.paste()  # Get clipboard content
    if clipboard_data:
        conn.sendall(f'TEXT\n{clipboard_data}'.encode('utf-8'))
    else:
        conn.sendall(b'NO CONTENT')

def start_server(host='0.0.0.0', port=65432):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, port))
        s.listen()
        print(f"Listening on {host}:{port}...")
        
        while True:  # Keep server running
            conn, addr = s.accept()
            with conn:
                print(f"Connected by {addr}")
                while True:  # Keep connection open
                    try:
                        send_clipboard_content(conn)
                        break  # Exit loop to accept new connection
                    except ConnectionResetError:
                        print("Connection closed.")
                        break  # Exit loop if the client disconnects

if __name__ == "__main__":
    start_server()
