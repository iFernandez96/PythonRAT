import socket
import base64

# Constants must be in sync with the server
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

if __name__ == "__main__":
    # Start the socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((socket.gethostname(), PORT))

    # Receive welcome message from server
    fullMessage = ''
    newMessage = True
    print(retrieveMessage(s))

    # Ask the user what they want to do
    print("What would you like to do?")
    print("1. Execute a command")
    print("2. Upload a file")
    print("3. Download a file")
    userInput = input()

    if userInput == "1":
        # Ask the user for the command to execute
        print("Please enter the command you want to execute:")
        command = input()

        # Send the command to the server
        messageForServer = "execute " + command
        messageForServer = f'{len(messageForServer):<{HEADERSIZE}}' + messageForServer
        s.send(bytes(messageForServer, "UTF-8"))

    elif userInput == "2":
        # Ask the user for the name of the file to upload
        print("Please enter the name of the file you want to upload:")
        fileName = input()

        # Read the contents of the file and encode it in base64
        with open(fileName, "rb") as file:
            b64String = base64.b64encode(file.read())

        # Send the file to the server
        messageForServer = "upload " + b64String + " " + fileName
        messageForServer = f'{len(messageForServer):<{HEADERSIZE}}' + messageForServer
        s.send(bytes(messageForServer, "UTF-8"))

    elif userInput == "3":
        # Send the download request to the server
        print("Seletected to Download")
        messageForServer = "download"
        messageForServer = f'{len(messageForServer):<{HEADERSIZE}}' + messageForServer
        s.send(bytes(messageForServer, "UTF-8"))

        # Receive the list of files from the server
        fullMessage = ''
        newMessage = True
        print(retrieveMessage(s))
           
