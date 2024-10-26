import socket
import os
import sys  # Import sys for command-line arguments

def receive_clipboard_content(host, port=65432):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((host, port))
        data = s.recv(1024).decode('utf-8')
        
        if data.startswith('TEXT'):
            clipboard_content = data[5:]  # Extract content
            print("Received Text:", clipboard_content)
            # Here you can set the clipboard in Windows using pyperclip
            import pyperclip
            pyperclip.copy(clipboard_content)

        elif data == 'NO CONTENT':
            print("No content available in the clipboard.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python clipboard_client.py <Hyper-V_VM_IP>")
        sys.exit(1)
    
    vm_ip = sys.argv[1]
    receive_clipboard_content(vm_ip)
