import multiprocessing
import socket
import threading
import re
import time

auto_join_port = 49153
send_messages_port = 49153
receive_messages_port = 51000
receive_new_server_port = 52000

class Client(multiprocessing.Process):

    registered_server = None
    host = socket.gethostname()
    client_address = socket.gethostbyname(host)

    def get_username(self):
        while True:
            username = input("Enter your username: ").strip()
            if username and len(username) <= 20:  # Basic validation
                return username
            print("Please enter a valid username (max 20 characters)")

    def run(self):
        print(f"{self.username}: Starting chat...")
        
        # Auto-join by sending broadcast
        self.auto_join()
        
        # Start sending and receiving messages
        send_messages_thread = threading.Thread(target=self.send_message)
        receive_messages_thread = threading.Thread(target=self.receive_messages)
        receive_new_server_thread = threading.Thread(target=self.receive_new_server)

        send_messages_thread.start()
        receive_messages_thread.start()
        receive_new_server_thread.start()

        send_messages_thread.join()
        receive_messages_thread.join()

    def auto_join(self):
        PORT = auto_join_port
        retries = 3
        
        while retries > 0:
            try:
                # Include username in the join message
                join_message = f"join|MAIN_CHAT|{self.username}"
                MSG = join_message.encode('utf-8')

                broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                broadcast_socket.settimeout(5)
                
                print(f"{self.username}: Attempting to join with server (attempt {4-retries}/3)")
                broadcast_socket.sendto(MSG, ('<broadcast>', PORT))
                
                data, server = broadcast_socket.recvfrom(1024)
                
                ip_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
                matches = re.findall(ip_pattern, data.decode('utf-8'))
                if matches:
                    self.registered_server_address = matches[1]
                    print(f"{self.username}: Successfully reconnected as {self.username}")
                    print(f"{self.username}: Connected to server: {self.registered_server_address}")
                    return True
                else:
                    print(f"{self.username}: Failed to extract server address from response")
                    
            except socket.timeout:
                retries -= 1
                if retries > 0:
                    print(f"Connection failed. Retrying... ({retries} attempts left)")
                    time.sleep(2)
                else:
                    print("Unable to connect to any server after multiple attempts")
                    return False
            except Exception as e:
                print(f"Error in auto_join: {e}")
                retries -= 1
                if retries > 0:
                    print(f"Retrying... ({retries} attempts left)")
                    time.sleep(2)
                else:
                    return False
            finally:
                try:
                    broadcast_socket.close()
                except:
                    pass
                
    def send_message(self):
        PORT = send_messages_port

        while True:
            try:
                message = input()
                if message.lower() == 'exit':
                    print(f"{self.username}: Shutting down chat client")
                    break

                retries = 5
                while retries > 0:
                    try:
                        send_message_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        send_message_socket.settimeout(5)
                        send_message_socket.connect((self.registered_server_address, PORT))
                        
                        # Send message with username
                        full_message = f"{self.username}|{message}"
                        send_message_socket.sendall(full_message.encode('utf-8'))
                        send_message_socket.close()
                        break  # Success, exit retry loop
                        
                    except (ConnectionRefusedError, socket.timeout):
                        retries -= 1
                        if retries > 0:
                            print(f"Connection failed. Retrying... ({retries} attempts left)")
                            time.sleep(2)  # Wait before retry
                        else:
                            print("Server appears to be down. Waiting for new server notification...")
                    except Exception as e:
                        print(f"Error sending message: {e}")
                        break
                    finally:
                        try:
                            send_message_socket.close()
                        except:
                            pass

            except Exception as e:
                print(f"Error in message input: {e}")
                break

    def receive_messages(self):
        PORT = receive_messages_port

        receive_message_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        receive_message_socket.bind((self.client_address, PORT))
        receive_message_socket.listen()

        print(f"{self.username}: Listening for messages")

        while True:
            connection, addr = receive_message_socket.accept()
            message = connection.recv(1024).decode('utf-8')
            print(message)  # Message already includes username from server

    def receive_new_server(self):
        PORT = receive_new_server_port
        try:
            receive_message_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            receive_message_socket.bind((self.client_address, PORT))
            receive_message_socket.listen()

            print(f"{self.username}: Listening for server address messages")

            while True:
                try:
                    connection, addr = receive_message_socket.accept()
                    new_server = connection.recv(1024).decode('utf-8')
                    print(f"{self.username}: Switching to new server: {new_server}")
                    self.registered_server_address = new_server
                    
                    # Try to re-establish connection with new server
                    print(f"{self.username}: Attempting to reconnect to new server at {new_server}")
                    self.auto_join()
                    
                except Exception as e:
                    print(f"Error receiving server update: {e}")
                finally:
                    try:
                        connection.close()
                    except:
                        pass
        except Exception as e:
            print(f"Error setting up server update listener: {e}")

    def __init__(self):
        self.host = socket.gethostname()
        self.client_address = socket.gethostbyname(self.host)

        # Get username at startup
        self.username = self.get_username()
        self.run()
