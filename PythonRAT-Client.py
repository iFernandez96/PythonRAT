import socket
HEADERSIZE = 10

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((socket.gethostname(), 64209))


while True:
    fullMessage = ''
    newMessage = True
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
            newMessage = True
            fullMessage = ''