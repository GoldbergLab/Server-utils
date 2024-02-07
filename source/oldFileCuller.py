from pathlib import Path
import re
import argparse
import datetime as dt
import numpy as np
from pprint import pprint
import yaml, json

# Search recursively through the given root directory for files, filtered into
#   two lists by two regex patterns: files to keep, and files to delete. Files
#   to delete are then matched up to files to delete by a keep match regex and
#   a delete match regex. The match (or 1st capturing group, if it exists) for
#   the keep match regex must be identical to the match (or 1st group) for the
#   delete match regex in order for two files to be considered a pair. The
#   results may be further filtered by requiring the keep and delete files to be
#   in the same folder for them to match.

def buildDirectoryStructure(fileList, structure={}, fileKey=lambda f:f, outputFunc=lambda obj:obj):
    for obj in fileList:
        file = fileKey(obj).resolve()
        thisStruct = structure
        parents = list(file.parents)
        for lvl, parent in enumerate(parents[-2::-1]):
            if not parent.name in thisStruct:
                thisStruct[parent.name] = {}
            thisStruct = thisStruct[parent.name]
        thisStruct[file.name] = outputFunc(obj)
    return structure

def abbreviatePath(path, width):
    if len(path) > width:
        ellipsis = '...'
        startLength = 3
        return path[:startLength] + ellipsis + path[(startLength+(len(path)-width+len(ellipsis))):]
    else:
        return path

def printResultSummary(root, nResults, keepList, unmatchedDeleteFiles, multipleKeepMatches, otherList, folderList, keepFilterPatternString, deleteFilterPatternString, keepMatchPatternString, deleteMatchPatternString, requireSameFolder):
    print()
    print("Searched root folder {r}".format(r=root.absolute()))
    print("\tKeepable filter pattern:         {kfp}".format(kfp=keepFilterPatternString))
    print("\tDeletable filter pattern:        {dfp}".format(dfp=deleteFilterPatternString))
    print("\tKeepable matching pattern:       {kmp}".format(kmp=keepMatchPatternString))
    print("\tDeletable matching pattern:      {dmp}".format(dmp=deleteMatchPatternString))
    print("\tOnly match within same folder?   {rsf}".format(rsf=requireSameFolder))
    print()
    print("RESULTS:")
    print("\t{d} deletable files identified (will be deleted)".format(d=nResults))
    print()
    print("\t{u} deletable files without a keep match (will NOT be deleted)".format(u=len(unmatchedDeleteFiles)))
    print("\t{k} keepable files identified".format(k=len(keepList)))
    print("\t{m} deletable files with multiple keep matches identified".format(m=len(multipleKeepMatches)))
    print("\t{o} unrelated files found {exts}".format(o=len(otherList), exts=set([f.suffix for f in otherList])))
    print("\t{f} folders found".format(f=len(folderList)))
    print()

parser = argparse.ArgumentParser(description='Search recursively through   \
the given root directory for files, and delete one file in pairs matched   \
according to provided regular expressions. Found files are filtered into \
two lists by two regex patterns: keepFilterPattern, and deleteFilterpattern. \
Files in the delete list are then paired up to files in the keep list by a \
keep match regex and a delete match regex. The match (or 1st capturing group, \
if it exists) for the keep match regex must be identical to the match (or 1st \
group) for the delete match regex in order for two files to be considered a \
pair. The results may be further filtered by requiring the keep and delete \
files to be in the same folder for them to match. The user then has the option \
to delete the paired files from the delete list.\n \
For example: \
python deduplicator.py -k .*\.avi -d .*\.cine -K (.*?)(_C[0-9]+L?)?\.avi -D (.*)\.cine "C:\path\\to\\root\\folder"\n \
would search for files with the avi extension to keep, and files with the cine extension to delete. The files would be matched \
if the filenames match without extensions, and the avi file may have a suffix of the form _C123L that the cine file does not include.',
epilog='Created by Brian Kardon, bmk27@cornell.edu')
parser.add_argument('root', metavar='root', type=str,
                    help='the root directory in which to search for files')
parser.add_argument('-c', '--cutoffAge', dest="cutoffAge", type=float, required=True, help='Cutoff age such that files older than this will be deleted')
# parser.add_argument('-d', '--deleteFilterPattern', dest="deleteFilterPattern", type=str, required=True, help='Regex pattern to filter files into the delete list')
# parser.add_argument('-K', '--keepMatchPattern', dest="keepMatchPattern", type=str, required=True, help='Regex pattern to extract matching segment from keep files to pair with delete files')
# parser.add_argument('-D', '--deleteMatchPattern', dest="deleteMatchPattern", type=str, required=True, help='Regex pattern to extract matching segment from delete files to pair with keep files')
parser.add_argument('-n', '--dryRun', dest="dryRun", action='store_true', help='If this flag is present, merely list the files that would be deleted, but do not delete them')
# parser.add_argument('-w', '--maxWidth', dest="maxWidth", type=int, default=160, help='Maximum number of characters to display full file paths to delete')
args = parser.parse_args()

print()

root = Path(args.root) # Path(r'Z:\video\Head-Fix Lick Experiments\ALM\Video\ALM_4\200510_lickLong_Day2_ALM_phase2_median')
dryRun = args.dryRun # True
cutoffAge = dt.timedelta(days=args.cutoffAge)

# # Require that the corresponding keep/delete files be in the same folder?
#
# # Pattern to use to filter filenames for keeping
# keepFilterPatternString = args.keepFilterPattern # '.*\.avi'
# # Pattern to use to filter filenames for deleting
# deleteFilterPatternString = args.deleteFilterPattern # '.*\.cine'
# # Pattern to use to match keep files to delete files. If there is no group, the whole match is used. If there is one or more gruops, the first group is used to match.
# keepMatchPatternString = args.keepMatchPattern # '(.*?)(?:_C[0-9]+L?)?\.avi'
# # Pattern to use to match delete files to keep files. If there is no group, the whole match is used. If there is one or more gruops, the first group is used to match.
# deleteMatchPatternString = args.deleteMatchPattern # '(.*)\.cine'


# keepFilterPattern = re.compile(keepFilterPatternString)
# deleteFilterPattern = re.compile(deleteFilterPatternString)
# keepMatchPattern = re.compile(keepMatchPatternString)
# deleteMatchPattern = re.compile(deleteMatchPatternString)

print("Searching for files...")
print()

now = dt.datetime.now()

fileList = []
folderList = []
k = 0
notifyInterval = 1000
for i in root.glob('**/*'):
    k = k + 1
    if k % notifyInterval == 0:
        print("...{k} files found...".format(k=k))
    if i.is_dir():
        folderList.append(i.resolve())
    else:
        fileList.append((i.resolve(), now - dt.datetime.fromtimestamp(i.stat().st_mtime)))

ages = np.array([r[1] for r in fileList])

filesToDelete = [(file, age) for file, age in fileList if age > cutoffAge]

oldestAge = max(ages)
meanAge = np.mean(ages)
medianAge = np.median(ages)
stdAge = dt.timedelta(seconds=np.std([age.total_seconds() for age in ages]))

print("{k} files found:".format(k=k))
print("\tOldest file age: {a}".format(a=oldestAge))
print("\tMedian file age: {a}".format(a=medianAge))
print("\tMean file age: {a} +/- {s}".format(a=meanAge, s=stdAge))
print()
print("# of files older than cutoff: {n}".format(n=len(filesToDelete)))

if len(filesToDelete) > 0:
    dirStruct = buildDirectoryStructure(filesToDelete, fileKey=lambda f:f[0], outputFunc=lambda obj:('{a} days'.format(a=obj[1].days)))

    print('******** FILES TO DELETE **********')
    print(yaml.dump(dirStruct, default_flow_style=False))
    print()
    for file in filesToDelete:
        print(file[0])
    # print(json.dumps(dirStruct, sort_keys=True, indent=4))
    print()
    yn = input('Delete files? ')
    if yn == 'y':
        for file, age in filesToDelete:
            if dryRun:
                print('Would have deleted ', file.name)
            else:
                print('Deleting ', file.name)
                file.unlink()
    else:
        print('Deletion cancelled.')
else:
    print('No files found to delete - exiting.')



#     unmatchedDeleteFiles = []
#     multipleKeepMatches = []
#
#     results = {}
#     for deleteFile, deleteMatch in deleteList:
#         if deleteMatch:
#             keepMatches = [keepFile for keepFile, keepMatch in keepList if (keepMatch == deleteMatch) and ((not requireSameFolder) or (keepFile.parents[0] == deleteFile.parents[0]))]
#             if len(keepMatches) > 0:
#                 results[deleteFile] = keepMatches
#             else:
#                 unmatchedDeleteFiles.append(deleteFile)
#             if len(keepMatches) > 1:
#                 # Uh oh, multiple matches
#                 multipleKeepMatches.append(deleteFile)
#     nResults = len(results)
#
#     for deleteFile in results:
#         print(    "Delete:     "+abbreviatePath(str(deleteFile.absolute()), maxWidth))
#         for keepFile in results[deleteFile]:
#             print("|--- Keep:  "+abbreviatePath(str(keepFile.absolute()), maxWidth))
#
#     print()
#     print("...done searching for files!")
#
#     printResultSummary(root, nResults, keepList, unmatchedDeleteFiles, multipleKeepMatches, otherList, folderList, keepFilterPatternString, deleteFilterPatternString, keepMatchPatternString, deleteMatchPatternString, requireSameFolder)
#
#     if nResults > 0:
#         while True:
#             cont = input("Type 'd' then press enter to continue with deletion process, or '?' for more info... ")
#             print()
#             if cont == '?':
#                 print()
#                 if len(unmatchedDeleteFiles) > 0:
#                     print('Unmatched deletable files found:')
#                 for file in unmatchedDeleteFiles:
#                     print('\t', file)
#                 print()
#                 if len(multipleKeepMatches) > 0:
#                     print('Deletable files with multiple matches found:')
#                 for file in multipleKeepMatches:
#                     print('\t', file)
#                 print()
#                 if len(otherList) > 0:
#                     print('Unrelated files found:')
#                 for file in otherList:
#                     print('\t', file)
#                 print()
#                 printResultSummary(root, nResults, keepList, unmatchedDeleteFiles, multipleKeepMatches, otherList, folderList, keepFilterPatternString, deleteFilterPatternString, keepMatchPatternString, deleteMatchPatternString, requireSameFolder)
#                 continue
#             break
#         if cont == 'd':
#             attempts = 0
#             maxAttempts = 5
#             while attempts < maxAttempts:
#                 sureText = "******************* Are you sure? If we go through with this, {n} files will be DELETED! *******************".format(n=nResults)
#                 print("*"*len(sureText))
#                 print(sureText)
#                 print("*"*len(sureText))
#                 print()
#                 keyPhrase = "I want to delete {n} files".format(n=nResults)
#                 ansPhrase = input("To continue, please type exactly '{keyPhrase}', then press enter, or press x to cancel: ".format(keyPhrase=keyPhrase))
#                 if ansPhrase==keyPhrase:
#                     print()
#                     print("Deletions confirmed! Proceeding...")
#                     fileNotFoundErrors = 0
#                     successfulDeletions = 0
#                     otherErrors = 0
#                     for k, deleteFile in enumerate(results):
#                         try:
#                             deleteFile.unlink()
#                             progressString = "{k}/{n}".format(k=k+1, n=nResults)
#                             if len(results[deleteFile]) > 1:
#                                 moreString = ' and {n} others'.format(n=len(results[deleteFile]-1))
#                             else:
#                                 moreString = ''
#                             print('{ps}: Deleting file:      {df}'.format(df=deleteFile.absolute(), ps=progressString))
#                             print('{sp}  because it matches: {kf}{ms}'.format(sp=" "*len(progressString), kf=results[deleteFile][0].absolute(), ms=moreString))
#                             successfulDeletions = successfulDeletions + 1
#                         except FileNotFoundError:
#                             fileNotFoundErrors = fileNotFoundErrors + 1
#                         except:
#                             otherErrors = otherErrors + 1
#                     print()
#                     print("Deletions complete! Summary:")
#                     print("{k} files successfully deleted, {fnf} files not found, {oe} other errors.".format(k=successfulDeletions, fnf=fileNotFoundErrors, oe=otherErrors))
#                     break
#                 elif ansPhrase=='x':
#                     print()
#                     print("Process cancelled, no files deleted.")
#                     print()
#                     break
#                 else:
#                     attempts = attempts + 1
#                     print("You did not type the correct key phrase. Please try again, or press 'x' to cancel. You have used {k} of {n} attempts.".format(k=attempts, n=maxAttempts))
#                     print()
#             if attempts == maxAttempts:
#                 print("Process cancelled, no files deleted.")
#         else:
#             print("Process cancelled, no files deleted.")
# except KeyboardInterrupt:
#     print("Process cancelled, no files deleted.")
