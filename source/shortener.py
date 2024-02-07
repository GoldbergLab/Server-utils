import sys
import os
from pathlib import Path
import os
import math

pathChars = 'abcdefghijklmnopqrstuvwxyz'

def toBase(n, b):
    if n == 0:
        return [0]
    pMax = int(math.log(n)/math.log(b))
    placeValues = []
    for p in range(pMax, -1, -1):
        v = n // (b**p)
        placeValues.append(v)
        n = n - v*(b**p)
    return placeValues

def getNameK(k):
    places = toBase(k, len(pathChars))
    name = ''
    for j in range(len(places)-1, -1, -1):
        name += pathChars[places[j]]
    return name

def shortenFolder(folderPath, dryRun=True):
    for k, subpath in enumerate(folderPath.iterdir()):
        if dryRun:
            print(subpath, ' ==> ', folderPath / getNameK(k))
        else:
            os.rename(subpath, folderPath / getNameK(k))
    for subpath in folderPath.iterdir():
        if subpath.is_dir():
            shortenFolder(subpath, dryRun=dryRun)

if __name__ == "__main__":
    # A utility to make all files and folders within a tree as short a name as possible.
    folderPath = Path(sys.argv[1])
    shortenFolder(folderPath, dryRun=False)
