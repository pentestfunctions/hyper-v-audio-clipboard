#!/usr/bin/env python3
import socket
import threading
import pyaudio
import time
import logging
from queue import Queue
import signal
import sys
import subprocess
import os

# Audio Configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100
AUDIO_PORT = 5001

def setup_audio_services():
    """Initialize and restart audio services"""
    try:
        logging.info("Setting up audio services...")
        
        # Kill any existing PulseAudio processes
        subprocess.run(['pulseaudio', '-k'], stderr=subprocess.PIPE)
        time.sleep(1)
        
        # Start PulseAudio if not running
        subprocess.run(['pulseaudio', '--start'], stderr=subprocess.PIPE)
        time.sleep(2)
        
        # Restart PipeWire services
        commands = [
            'systemctl --user restart pipewire',
            'systemctl --user restart pipewire-pulse',
            'systemctl --user restart pulseaudio'
        ]
        
        for cmd in commands:
            try:
                subprocess.run(cmd.split(), stderr=subprocess.PIPE)
                time.sleep(1)
            except Exception as e:
                logging.warning(f"Command failed: {cmd} - {e}")
        
        # Load audio modules if needed
        subprocess.run(['pactl', 'load-module', 'module-null-sink'], stderr=subprocess.PIPE)
        
        # Test audio system
        try:
            subprocess.run(['pactl', 'info'], check=True, stdout=subprocess.PIPE)
            logging.info("Audio services initialized successfully")
        except subprocess.CalledProcessError:
            logging.error("Failed to verify audio system")
            raise
            
    except Exception as e:
        logging.error(f"Error setting up audio services: {e}")
        raise

class AudioHandler:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.streams = {'input': None, 'output': None}
        
    def setup_streams(self):
        """Setup audio streams using default PulseAudio devices"""
        try:
            # Make sure audio services are running
            setup_audio_services()
            
            # List available devices for debugging
            self.list_devices()
            
            self.streams['input'] = self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                input_device_index=0,
                frames_per_buffer=CHUNK
            )
            logging.info("Input stream setup successfully using PulseAudio")
            
            self.streams['output'] = self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                output=True,
                output_device_index=0,
                frames_per_buffer=CHUNK
            )
            logging.info("Output stream setup successfully using PulseAudio")
            
        except Exception as e:
            logging.error(f"Error setting up streams: {e}")
            raise

    def list_devices(self):
        """List all available audio devices"""
        info = []
        for i in range(self.p.get_device_count()):
            try:
                device_info = self.p.get_device_info_by_index(i)
                info.append(f"Device {i}: {device_info['name']}")
                info.append(f"  Input channels: {device_info['maxInputChannels']}")
                info.append(f"  Output channels: {device_info['maxOutputChannels']}")
                info.append(f"  Default SampleRate: {device_info['defaultSampleRate']}")
                info.append("")
            except Exception as e:
                info.append(f"Error getting device {i} info: {e}")
        logging.info("Available audio devices:\n" + '\n'.join(info))

    def cleanup(self):
        for stream in self.streams.values():
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception as e:
                    logging.error(f"Error closing stream: {e}")
        self.p.terminate()

class AudioServer:
    def __init__(self, host='0.0.0.0'):
        self.host = host
        self.running = False
        self.clients = set()
        self.audio = AudioHandler()
        self.server_socket = None
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        logging.info("Shutdown signal received. Cleaning up...")
        self.stop()

    def handle_client(self, client_socket, address):
        logging.info(f"New client connected from {address}")
        self.clients.add(client_socket)
        
        def receive_audio():
            while self.running:
                try:
                    data = client_socket.recv(CHUNK * 4)
                    if not data:
                        break
                    
                    if self.audio.streams['output']:
                        try:
                            self.audio.streams['output'].write(data)
                        except Exception as e:
                            logging.error(f"Error playing audio: {e}")
                except Exception as e:
                    logging.error(f"Error receiving audio from {address}: {e}")
                    break
        
        def send_audio():
            while self.running:
                try:
                    if self.audio.streams['input']:
                        try:
                            data = self.audio.streams['input'].read(CHUNK, exception_on_overflow=False)
                            client_socket.send(data)
                        except Exception as e:
                            logging.error(f"Error reading from input: {e}")
                except Exception as e:
                    logging.error(f"Error sending audio to {address}: {e}")
                    break

        receive_thread = threading.Thread(target=receive_audio)
        send_thread = threading.Thread(target=send_audio)
        
        receive_thread.start()
        send_thread.start()
        
        receive_thread.join()
        send_thread.join()
        
        self.clients.remove(client_socket)
        client_socket.close()
        logging.info(f"Client {address} disconnected")

    def start(self):
        self.running = True
        
        try:
            # Initialize audio services and streams
            self.audio.setup_streams()
            
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            self.server_socket.bind((self.host, AUDIO_PORT))
            self.server_socket.listen(5)
            logging.info(f"Audio server listening on {self.host}:{AUDIO_PORT}")
            
            while self.running:
                try:
                    client_socket, address = self.server_socket.accept()
                    client_thread = threading.Thread(
                        target=self.handle_client,
                        args=(client_socket, address)
                    )
                    client_thread.start()
                except Exception as e:
                    if self.running:
                        logging.error(f"Error accepting connection: {e}")
                    
        except Exception as e:
            logging.error(f"Server error: {e}")
        finally:
            self.stop()

    def stop(self):
        self.running = False
        
        # Close all client connections
        for client in self.clients:
            try:
                client.close()
            except:
                pass
        self.clients.clear()
        
        # Close server socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
            
        # Cleanup audio
        self.audio.cleanup()

if __name__ == "__main__":
    # Ensure script is run with sudo if needed
    if os.geteuid() != 0:
        logging.warning("This script might need sudo privileges to manage audio services")
        logging.warning("If you experience issues, try running with sudo")
    
    server = AudioServer()
    server.start()
