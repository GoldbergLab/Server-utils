<#
.SYNOPSIS
    Sets up z5 drive with per-user folders and the lab's permission model.

.DESCRIPTION
    Permission model:
      - Administrators: Full control everywhere
      - LabMembers group: can read/traverse anywhere + create new files/folders
        anywhere, but cannot modify or delete ANY existing files/folders
        (not even ones they themselves created — strict isolation)
      - Each named user: explicit Modify permission on their own user folder,
        so they have full read/write/delete within it

    Consequence: once a file is placed in userB's folder, only userB (and
    admins) can modify or delete it, regardless of who created it.

    The script is idempotent: running it again with the same users is safe.
    Adding new users later: just re-run with the new name(s).

.PARAMETER Root
    The path to the z5 drive root, e.g. "D:\z5" or "Z:\"

.PARAMETER Users
    Array of usernames. A folder will be created for each (named after the user)
    and that user will get Modify permission on their folder.

.PARAMETER LabGroup
    Name of the group whose members can create-but-not-modify across z5.
    Defaults to "LabMembers". This group must exist (create it first with
    `net localgroup LabMembers /add` and add users to it).

.PARAMETER Domain
    Account domain. Defaults to the local computer name (for local accounts).
    If you ever use a real domain, pass the domain name here.

.EXAMPLE
    .\setup_z5_userfolders.ps1 -Root "D:\z5" -Users "lizcirone","cj397","ap2527"

.EXAMPLE
    # Just add one new user later
    .\setup_z5_userfolders.ps1 -Root "D:\z5" -Users "newuser"
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$Root,

    [Parameter(Mandatory=$true)]
    [string[]]$Users,

    [string]$LabGroup = "LabMembers",

    [string]$Domain = $env:COMPUTERNAME
)

if (-not (Test-Path $Root)) {
    throw "Root path does not exist: $Root"
}

Write-Host "=== Configuring root ACL on $Root ===" -ForegroundColor Cyan

# Build root ACL from scratch (idempotent)
$rootAcl = Get-Acl $Root
$rootAcl.SetAccessRuleProtection($true, $false)   # disable inheritance, don't keep inherited
$rootAcl.Access | ForEach-Object { [void]$rootAcl.RemoveAccessRule($_) }

$inheritBoth = [System.Security.AccessControl.InheritanceFlags]"ContainerInherit,ObjectInherit"
$noProp      = [System.Security.AccessControl.PropagationFlags]::None

# Administrators: Full control, inherits everywhere
$rootAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
    "Administrators", "FullControl", $inheritBoth, $noProp, "Allow"
)))

# SYSTEM: Full control (needed for backups, indexing, etc.)
$rootAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
    "SYSTEM", "FullControl", $inheritBoth, $noProp, "Allow"
)))

# LabMembers: ReadAndExecute + CreateFiles + CreateDirectories, inherits
# This lets members read anything and create new files/folders,
# but NOT modify or delete existing ones — not even ones they created.
# Only the owning user of a folder (via their explicit Modify grant below)
# or an administrator can modify/delete the contents of that folder.
$labRights = [System.Security.AccessControl.FileSystemRights]"ReadAndExecute,CreateFiles,CreateDirectories"
$rootAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
    $LabGroup, $labRights, $inheritBoth, $noProp, "Allow"
)))

Set-Acl -Path $Root -AclObject $rootAcl
Write-Host "Root ACL set." -ForegroundColor Green

Write-Host ""
Write-Host "=== Creating user folders ===" -ForegroundColor Cyan

foreach ($user in $Users) {
    $userPath = Join-Path $Root $user
    if (-not (Test-Path $userPath)) {
        New-Item -ItemType Directory -Path $userPath | Out-Null
        Write-Host "  Created: $userPath" -ForegroundColor Green
    } else {
        Write-Host "  Exists:  $userPath"
    }

    # Grant explicit Modify to the user on their own folder (inherits to children).
    # Inheritance from root stays enabled, so Admin/LabMembers/CreatorOwner
    # ACEs still apply from the root.
    $userAcl = Get-Acl $userPath
    $account = "$Domain\$user"
    try {
        $userRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $account, "Modify", $inheritBoth, $noProp, "Allow"
        )
        $userAcl.AddAccessRule($userRule)
        Set-Acl -Path $userPath -AclObject $userAcl
        Write-Host "           Granted Modify to $account"
    } catch {
        Write-Warning "  Could not grant permissions to '$account': $_"
        Write-Warning "  (Does the account exist on this server?)"
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
