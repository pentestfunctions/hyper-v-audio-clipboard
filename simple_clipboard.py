import socket
import pyperclip

def start_server(host='0.0.0.0', port=65432):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, port))
        s.listen()
        print(f"Listening on {host}:{port}...")
        
        conn, addr = s.accept()  # Accept a single connection
        with conn:
            print(f"Connected by {addr}")
            while True:  # Keep receiving data
                data = conn.recv(1024).decode('utf-8')
                if not data:  # If connection is closed
                    break
                
                if data.startswith('TEXT'):
                    clipboard_content = data[5:]  # Extract content
                    print("Received Clipboard Text:", clipboard_content)
                    pyperclip.copy(clipboard_content)  # Copy received text to clipboard

if __name__ == "__main__":
    start_server()
