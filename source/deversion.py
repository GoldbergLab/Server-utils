import sys
from pathlib import Path
import re
import shutil
import multiprocessing as mp
import queue
import time
import traceback

# When Cloudberry backs up a file, it replaces it with a directory of the same name plus a characteristic ending string
#   Inside that folder, it puts one or more version folders named as YYYYMMDDHHMMSS
#   Inside those folders are the actual file versions, with their original filenames.
#   If a file is deleted, Cloudbery signifies that by saving a 0 byte file with the name "cbbdeleted" in place of the file version.

#deletedFileRegex = re.compile("^cbbdeleted$")
deletedFileName = "cbbdeleted"
versionDateFolderRegex = re.compile("^[0-9]{14}")

def isVersionFolder(folderPath):
    return folderPath.name[-1] == "："

def isFileDeleted(versionPath):
    return versionPath.name == deletedFileName

class FileCopyWorker(mp.Process):
    # A process for copying files in parallel.
    # Listens for (src, dst) pairs and copies them as they are received.

    EXIT = 'exit'

    def __init__(self, dryRun=False, ID=None):
        mp.Process.__init__(self, daemon=True)
        self.queue = mp.Queue(maxsize=100)
        self.dryRun = dryRun
        self.ID = ID
        self.failedCount = 0

    def run(self):
        while True:
            msg = ''
            try:
                msg = self.queue.get(block=True)
            except queue.Empty:
                pass

            if msg == '':
                pass
            elif msg == FileCopyWorker.EXIT:
                print('Worker {ID}: Exiting!'.format(ID=self.ID))
                break
            else:
                # Message should be a tuple of two paths
                srcPath, dstPath = msg
                if dstPath.exists():
                    print("\tWorker {ID}: Restored file already exists...skipping: {dst}".format(ID=self.ID, dst=dstPath))
                else:
                    if self.dryRun:
                        print("\tWorker {ID}: Dry run: Copy {src} to {dst}".format(ID=self.ID, src=srcPath, dst=dstPath))
                    else:
                        print("\tWorker {ID}: Copy {src} to {dst}".format(ID=self.ID, src=srcPath, dst=dstPath))
                        try:
                            shutil.copy(srcPath, dstPath)
                        except KeyboardInterrupt:
                            print("\tWorker {ID} received keyboard interrupt. Exiting.".format(ID=self.ID))
                            break;
                        except:
                            print("\tFAILED: Worker {ID}: Copy {src} to {dst}".format(ID=self.ID, src=srcPath, dst=dstPath))
                            self.failedCount += 1

class FileCopyWorkerPool():
    def __init__(self, numWorkers, dryRun=False):
        self.numWorkers = numWorkers
        self.pool = []
        for k in range(self.numWorkers):
            self.pool.append(FileCopyWorker(dryRun=dryRun, ID=k))
        for worker in self.pool:
            worker.start()
        self.workerIndex = 0
        self.isClosed = False

    def getAverageWorkload(self):
        jobs = 0
        for worker in self.pool:
            jobs += worker.queue.qsize()
        return jobs / len(self.pool)

    def copyFile(self, srcPath, dstPath):
        if self.isClosed:
            raise IOError('FileCopyWorkerPool is closed and cannot be used any more.')
        self.pool[self.workerIndex].queue.put((srcPath, dstPath))
        self.workerIndex = (self.workerIndex + 1) % self.numWorkers
        #print('\tAverage worker pool workload: {L}'.format(L=self.getAverageWorkload()))

    def closePool(self):
        for workerIndex in range(len(self.pool)):
            self.closeWorker(workerIndex)
        self.isClosed = True

    def closeWorker(self, workerIndex):
        self.pool[workerIndex].queue.put(FileCopyWorker.EXIT)

    def waitForWorkersToClose(self):
        print('Waiting for workers to close...')
        if not self.isClosed:
            raise IOError('Close workers first before waiting for them to finish')
        # Join each worker in turn and wait for them to finish.
        failedCount = 0
        for worker in self.pool:
            print('Waiting for worker {ID} to close...'.format(ID=worker.ID))
            worker.join()
            failedCount += worker.failedCount
        print('All workers closed.')
        return failedCount

def restoreLatestVersion(versionFolderPath, restorePath, restoreDeletionsAfterDate=None, workerPool=None, dryRun=False):
    # versionFolderPath is the path to the version folder
    # restorePath is the path to the current restore directory where the latest version should be copied to
    failed = False
    killFlag = False
    latestVersionPath, deletionDate, numVersions = getLatestVersionPath(versionFolderPath)
    if deletionDate is not None:
        # File has been deleted
        if restoreDeletionsAfterDate is not None:
            if deletionDate > restoreDeletionsAfterDate:
                restore = True
                print("Restoring deleted file: {p}".format(p=versionFolderPath))
            else:
                print("Ignoring deleted file because date was {d}".format(d=deletionDate))
                restore = False
        else:
            restore = False
    else:
        restore = True

    if restore:
        if workerPool is not None:
            workerPool.copyFile(latestVersionPath, restorePath / latestVersionPath.name)
        else:
            if restorePath.exists():
                print("Restored file already exists...skipping: {p}".format(p=restorePath))
            else:
                if dryRun:
                    print("Dry run: Copy {p} to {q}".format(p=latestVersionPath, q=restorePath / latestVersionPath.name))
                else:
                    print("Copy {p} to {q}".format(p=latestVersionPath, q=restorePath / latestVersionPath.name))
                    try:
                        shutil.copy(latestVersionPath, restorePath / latestVersionPath.name)
                        failed = False
                    except KeyboardInterrupt:
                        killFlag = True
                        failed = True
                        print('Keyboard interrupt received...attempting to exit gracefully...')
                    except:
                        print("FAILED: Copy {p} to {q}".format(p=latestVersionPath, q=restorePath / latestVersionPath.name))
                        failed = True
    else:
        print("Ignoring deleted file: {p}".format(p=versionFolderPath))
    return restore, numVersions, failed, latestVersionPath, killFlag

def getLatestVersionPath(versionFolderPath):
    # versionFolderPath is a path to a folder representing a backed up file,
    #   containing date-named subfolders, which contain the actual file verisons.
    # Returns:
    #   latestVersionPath: Path to latest version file
    #   deletionDate: string date (YYYYMMDDHHMMSS) indicating deletion date if
    #               this file has been marked deleted, None if not
    #   versionCount: int number of versions found

    deletionDate = None
    latestVersionPath = None
    versionDatePaths = list(sorted(versionFolderPath.iterdir(), key=lambda p:p.name, reverse=True))
    versionCount = len(versionDatePaths)
    # Loop over file versions in reverse chronological order
    for versionDatePath in versionDatePaths:
        # Get first (and should be only) file in version folder
        latestVersionPath = next(versionDatePath.iterdir())
        if isFileDeleted(latestVersionPath):
            # It's a deletion marker
            versionCount -= 1
            deletionDate = versionDatePath.name
            continue
        else:
            # It's an actual version, not a deletion marker
            break
    return latestVersionPath, deletionDate, versionCount

    if isFileDeleted(versionDatePaths[-1]):
        isDeleted = True
        if len(versionDatePaths) < 2:
            raise IOError("This file is deleted, but there are no previous versions. That doesn't really make sense: {f}".format(f=versionFolderPath))
        files = list(versionDatePaths[-2].iterdir())
        if len(files) > 1:
            raise IOError("More than one file found within version date subfolder. That shouldn't happen: {p}".format(p=latestVersionDatePath))
        latestVersionPath = files[0]


    for subpath in versionFolderPath.iterdir():
        if versionDateFolderRegex.match(subpath.name):
            versionCount += 1
            versionDate = int(subpath.name)
            if versionDate > latestVersionDate:
                latestVersionDate = versionDate
                latestVersionDatePath = subpath
        else:
            raise IOError("Version date subfolder does not match expected pattern: {p}".format(p=subpath))
    files = list(latestVersionDatePath.iterdir())
    if len(files) > 1:
        raise IOError("More than one file found within version date subfolder. That shouldn't happen: {p}".format(p=latestVersionDatePath))
    latestVersionPath = files[0]
    isDeleted = isFileDeleted(latestVersionPath)
    return [latestVersionPath, isDeleted, versionCount]

def restoreFolder(backupPath, restorePath, restoreDeletionsAfterDate=None, numWorkers=4, skipDirectories=[], workerPool=None, deleteEmptyDirectories=True, dryRun=False):
    folderCount=0
    fileCount=0
    versionCount=0
    deletedFileCount=0
    deletedFolderCount=0
    failedCount=0
    failList = []
    killFlag = False

    try:
        if workerPool is None:
            # No worker pool passed in, maybe create it?
            if numWorkers is None:
                # Nope, don't create it
                workerPool = None
                closeWorkersWhenDone = False
            else:
                # Yep, create it
                workerPool = FileCopyWorkerPool(numWorkers, dryRun=dryRun)
                closeWorkersWhenDone = True
        else:
            closeWorkersWhenDone = False

        if not dryRun and not restorePath.exists():
            print("Restore path not found! {p}".format(p=restorePath))
        for subpath in backupPath.iterdir():
            if len(str(subpath)) > 260:
                print('ERROR: path is too long! Skipping and adding to failed file list: {tl}'.format(tl=subpath))
                failedCount += 1
                failList.append(subpath)
                continue
            if subpath.is_dir():
                # Should always be true with a Cloudberry backup until we get to a version folder
                if isVersionFolder(subpath):
                    # Restore latest version of file
                    try:
                        restore, numVersions, failed, latestVersionPath, subKillFlag = restoreLatestVersion(subpath, restorePath, restoreDeletionsAfterDate=restoreDeletionsAfterDate, workerPool=workerPool, dryRun=dryRun)
                        if not restore:
                            deletedFileCount += 1
                        if failed:
                            failedCount += 1
                            failList.append(latestVersionPath)
                        versionCount += numVersions
                        fileCount += 1
                        if subKillFlag:
                            raise KeyboardInterrupt
                    except FileNotFoundError:
                        print('ERROR: Could not access folder (maybe path is too long?) {f}'.format(f=subpath))
                        failedCount += 1
                else:
                    if subpath in skipDirectories:
                        print('Skipping directory at user request: {sd}'.format(sd=subpath))
                        skipDirectories.remove(subpath)
                        continue;
                    else:
                        print('Not skipping directory at user request: {sd}'.format(sd=subpath))
                    # Create subfolder in restore directory
                    nextRestorePath = restorePath / subpath.name
                    folderCount = folderCount + 1
                    if dryRun:
                        print("DryRun: Create directory {p}".format(p=nextRestorePath))
                    else:
                        nextRestorePath.mkdir(exist_ok=True)
                        print("Create directory {p}".format(p=nextRestorePath))

                    # Recurse
                    subFolderCount, subFileCount, subVersionCount, subDeletedFileCount, subDeletedFolderCount, failedCount, newFailList, subKillFlag = restoreFolder(subpath, nextRestorePath, restoreDeletionsAfterDate=restoreDeletionsAfterDate, skipDirectories=skipDirectories, workerPool=workerPool, deleteEmptyDirectories=deleteEmptyDirectories, dryRun=dryRun)
                    if subKillFlag:
                        raise KeyboardInterrupt
                    if deleteEmptyDirectories and (subFileCount == subDeletedFileCount):
                        print('All files in this directory are deleted...deleting directory: {p}'.format(p=subpath))
                        if dryRun:
                            print('Dry run: Deleting empty restored directory: {p}'.format(p=subpath))
                        else:
                            nextRestorePath.rmdir()
                            print('Deleting empty restored directory: {p}'.format(p=subpath))
                        deletedFolderCount += 1

                    # Update counts from recursion
                    deletedFolderCount += subDeletedFolderCount
                    folderCount += subFolderCount
                    fileCount += subFileCount
                    versionCount += subVersionCount
                    deletedFileCount += subDeletedFileCount
                    failList += newFailList
            else:
                raise IOError("Um there shouldn't be plain files here, something weird happened: {p}".format(p=subpath))

        if closeWorkersWhenDone:
            workerPool.closePool()
            workerFailedCount = workerPool.waitForWorkersToClose()
            failedCount += workerFailedCount
    except KeyboardInterrupt:
        if not subKillFlag:
            print('Keyboard interrupt received, attempting to exit gracefully...')
        killFlag = True
    except:
        print('Restore folder failed with errors:')
        traceback.print_exc()
        print('Attempting to continue...')

    return folderCount, fileCount, versionCount, deletedFileCount, deletedFolderCount, failedCount, failList, killFlag

if __name__ == "__main__":
    # A utility to un-version a backup made by Cloudberry to Backblaze B2, since Cloudberry can't seem to do it.
    backupRootPath = Path(sys.argv[1])
    restoreRootPath = Path(sys.argv[2])
    skipDirectories = []
    restoreDeletionsAfterDate = None
    if len(sys.argv) > 3:
        skipFile = Path(sys.argv[3])
        with open(skipFile, 'r') as f:
            for line in f.readlines():
                skipDirectories.append(Path(line.strip()))
        print('Skip directories loaded:')
        for skipDir in skipDirectories:
            print('\t', skipDir)
    if len(sys.argv) > 4:
        restoreDeletionsAfterDate = sys.argv[4]

    dryRun = False

    folderCount, fileCount, versionCount, deletedFileCount, deletedFolderCount, failedCount, failList, killFlag = restoreFolder(backupRootPath, restoreRootPath, restoreDeletionsAfterDate=restoreDeletionsAfterDate, skipDirectories=skipDirectories, numWorkers=4, dryRun=dryRun)

    if dryRun:
        print('Dry run:')
    print('Restore report:')
    print('     Folders found:', folderCount)
    print('     Files restored:', fileCount)
    print('     Versions found:', versionCount)
    print('     Deleted files:', deletedFileCount)
    print('     Deleted folders:', deletedFolderCount)
    print('     Failed access/copy count:', failedCount)
    print('     List of failed file restores:')
    for failedFile in failList:
        print('\t', failedFile)
