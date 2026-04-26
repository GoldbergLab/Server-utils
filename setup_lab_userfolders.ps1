<#
.SYNOPSIS
    Sets up z5 drive with per-user folders and the lab's permission model.

.DESCRIPTION
    Permission model:
      - Administrators: Full control everywhere
      - LabMembers group: can read/traverse anywhere + create new files/folders
        anywhere, but cannot modify or delete ANY existing files/folders
        (not even ones they themselves created -- strict isolation)
      - Reader account: read/traverse only, anywhere (intended for use as a
        base credential for CIFS `multiuser` mounts)
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

.PARAMETER ReaderUser
    Name of a read-only local account used as a base credential for CIFS
    `multiuser` mounts. The script will create the account if it does not
    already exist (you'll be prompted for a password). Defaults to "reader".

.PARAMETER Domain
    Account domain. Defaults to the local computer name (for local accounts).
    If you ever use a real domain, pass the domain name here.

.PARAMETER DryRun
    If set, the script reports what it would do at each step but makes no
    changes -- no accounts, groups, folders, or ACLs are created or modified,
    and you are not prompted for any passwords. Useful for previewing the
    effect of a run before committing to it (especially on TB-scale volumes,
    where the real ACL propagation can take hours).

.EXAMPLE
    .\setup_z5_userfolders.ps1 -Root "D:\z5" -Users "lizcirone","cj397","ap2527"

.EXAMPLE
    # Just add one new user later
    .\setup_z5_userfolders.ps1 -Root "D:\z5" -Users "newuser"

.EXAMPLE
    # Preview without making any changes
    .\setup_z5_userfolders.ps1 -Root "D:\z5" -Users "newuser" -DryRun
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$Root,

    [Parameter(Mandatory=$true)]
    [string[]]$Users,

    [string]$LabGroup = "LabMembers",

    [string]$ReaderUser = "reader",

    [string]$Domain = $env:COMPUTERNAME,

    [switch]$DryRun
)

if (-not (Test-Path $Root)) {
    throw "Root path does not exist: $Root"
}

$overallSw = [System.Diagnostics.Stopwatch]::StartNew()

# Track high-level progress so the finally block can summarize what was
# accomplished if the script exits early (crash, Ctrl+C, ACL error, etc.)
$status = @{
    LabGroupOk     = $false
    ReaderOk       = $false
    RootAclOk      = $false
    CompletedUsers = @()
    CurrentUser    = $null
}
$completed = $false

try {

if ($DryRun) {
    Write-Host "=== DRY RUN MODE -- no changes will be made ===" -ForegroundColor Magenta
    Write-Host "    All actions reported below are previews; nothing is written." -ForegroundColor Magenta
    Write-Host ""
}

Write-Host "=== Ensuring lab group '$LabGroup' exists ===" -ForegroundColor Cyan
if (-not (Get-LocalGroup -Name $LabGroup -ErrorAction SilentlyContinue)) {
    if ($DryRun) {
        Write-Host "  [DRY RUN] Would create local group: $LabGroup" -ForegroundColor Magenta
    } else {
        New-LocalGroup -Name $LabGroup `
                       -Description "Lab members with create-only access to shared drive" `
                       -ErrorAction Stop | Out-Null
        Write-Host "  Created local group: $LabGroup" -ForegroundColor Green
        Write-Warning "  The group is empty. Add members with:"
        Write-Warning "    Add-LocalGroupMember -Group $LabGroup -Member <username>"
        Write-Warning "  Until it has members, nobody (besides admins / folder owners / reader) can access the drive."
    }
} else {
    Write-Host "  Lab group already exists."
}
$status.LabGroupOk = $true

Write-Host ""
Write-Host "=== Ensuring reader account '$ReaderUser' exists ===" -ForegroundColor Cyan
if (-not (Get-LocalUser -Name $ReaderUser -ErrorAction SilentlyContinue)) {
    if ($DryRun) {
        Write-Host "  [DRY RUN] Would prompt for a password and create local user: $ReaderUser" -ForegroundColor Magenta
    } else {
        $pw = Read-Host -AsSecureString "Set a password for the new '$ReaderUser' account"
        New-LocalUser -Name $ReaderUser `
                      -Password $pw `
                      -FullName "$ReaderUser (read-only service account)" `
                      -Description "Read-only credential for CIFS mounts" `
                      -PasswordNeverExpires `
                      -UserMayNotChangePassword `
                      -ErrorAction Stop | Out-Null
        Write-Host "  Created local user: $ReaderUser" -ForegroundColor Green
    }
} else {
    Write-Host "  Reader account already exists."
}
$status.ReaderOk = $true

Write-Host ""
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
# but NOT modify or delete existing ones -- not even ones they created.
# Only the owning user of a folder (via their explicit Modify grant below)
# or an administrator can modify/delete the contents of that folder.
$labRights = [System.Security.AccessControl.FileSystemRights]"ReadAndExecute,CreateFiles,CreateDirectories"
$rootAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
    $LabGroup, $labRights, $inheritBoth, $noProp, "Allow"
)))

# Reader account: ReadAndExecute only, inherits everywhere.
# Intended for use as a base credential by CIFS `multiuser` mounts on clients.
$rootAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
    "$Domain\$ReaderUser", "ReadAndExecute", $inheritBoth, $noProp, "Allow"
)))

if ($DryRun) {
    Write-Host "  [DRY RUN] Would apply root ACL to $Root with these explicit ACEs:" -ForegroundColor Magenta
    Write-Host "             Administrators           : FullControl"
    Write-Host "             SYSTEM                   : FullControl"
    Write-Host "             $LabGroup                : ReadAndExecute, CreateFiles, CreateDirectories"
    Write-Host "             $Domain\$ReaderUser      : ReadAndExecute"
    Write-Host "             (inheritance: ContainerInherit + ObjectInherit, propagated to all children)"
    Write-Host "  [DRY RUN] On TB-scale volumes a real run can take hours for inheritance to propagate." -ForegroundColor Magenta
    $status.RootAclOk = $true
} else {
    Write-Host "    Applying new root ACL and propagating inheritance to every child." -ForegroundColor Yellow
    Write-Host "    On TB-scale volumes this can take hours. The script will appear to" -ForegroundColor Yellow
    Write-Host "    hang on this step -- that is expected; do not interrupt it." -ForegroundColor Yellow
    Write-Host "    Start: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    $rootSw = [System.Diagnostics.Stopwatch]::StartNew()

    Set-Acl -Path $Root -AclObject $rootAcl

    $rootSw.Stop()
    $status.RootAclOk = $true
    Write-Host ("Root ACL set. (elapsed: {0:N1} min / {1:N0} s)" -f $rootSw.Elapsed.TotalMinutes, $rootSw.Elapsed.TotalSeconds) -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Creating user folders ===" -ForegroundColor Cyan
Write-Host "    Note: each user-folder ACL change also propagates to that folder's children,"
Write-Host "    so users with very large folders may take a while on this step too."

$total = $Users.Count
$idx = 0
foreach ($user in $Users) {
    $idx++
    $status.CurrentUser = $user
    Write-Host ""
    Write-Host "  [$idx/$total] $user" -ForegroundColor Cyan
    $userSw = [System.Diagnostics.Stopwatch]::StartNew()
    $account = "$Domain\$user"

    # Ensure local account exists
    $userExists = [bool](Get-LocalUser -Name $user -ErrorAction SilentlyContinue)
    if (-not $userExists) {
        Write-Host "  Local account '$user' does not exist." -ForegroundColor Yellow
        if ($DryRun) {
            Write-Host "  [DRY RUN] Would prompt for a password and create local user: $user" -ForegroundColor Magenta
        } else {
            $pw = Read-Host -AsSecureString "  Set an initial password for new user '$user'"
            try {
                New-LocalUser -Name $user `
                              -Password $pw `
                              -FullName $user `
                              -Description "Lab member account" `
                              -PasswordNeverExpires `
                              -ErrorAction Stop | Out-Null
                Write-Host "  Created local user: $user" -ForegroundColor Green
                $userExists = $true
            } catch {
                Write-Warning "  Failed to create user '$user': $_"
                Write-Warning "  Skipping folder setup for this user."
                $status.CurrentUser = $null
                continue
            }
        }
    }

    $userPath = Join-Path $Root $user
    $folderExists = Test-Path $userPath
    if (-not $folderExists) {
        if ($DryRun) {
            Write-Host "  [DRY RUN] Would create folder: $userPath" -ForegroundColor Magenta
        } else {
            New-Item -ItemType Directory -Path $userPath | Out-Null
            Write-Host "  Created: $userPath" -ForegroundColor Green
            $folderExists = $true
        }
    } else {
        Write-Host "  Exists:  $userPath"
    }

    # Normalize the user folder ACL:
    #   1. Re-enable inheritance from root (clears "protected" flag)
    #   2. Remove any leftover explicit ACEs (legacy Users, etc.)
    #   3. Add a single explicit Modify for the owning user
    # After this, the folder inherits Admins/SYSTEM/LabMembers/reader from root,
    # plus the explicit Modify for the owner.
    if ($DryRun) {
        Write-Host "           [DRY RUN] Would normalize ACL on $userPath:" -ForegroundColor Magenta
        Write-Host "                       - re-enable inheritance from root" -ForegroundColor Magenta
        Write-Host "                       - remove any explicit (non-inherited) ACEs" -ForegroundColor Magenta
        Write-Host "                       - grant Modify to $account" -ForegroundColor Magenta
    } elseif (-not $folderExists) {
        # Shouldn't reach here unless folder creation failed silently; guard anyway
        Write-Warning "           Folder $userPath does not exist; skipping ACL step."
    } else {
        $userAcl = Get-Acl $userPath
        $userAcl.SetAccessRuleProtection($false, $false)  # enable inheritance, don't preserve current
        # Remove any explicit (non-inherited) access rules
        $userAcl.Access | Where-Object { -not $_.IsInherited } | ForEach-Object {
            [void]$userAcl.RemoveAccessRule($_)
        }
        try {
            $userRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                $account, "Modify", $inheritBoth, $noProp, "Allow"
            )
            $userAcl.AddAccessRule($userRule)
            Set-Acl -Path $userPath -AclObject $userAcl
            Write-Host "           Normalized ACL; granted Modify to $account"
        } catch {
            Write-Warning "  Could not grant permissions to '$account': $_"
            Write-Warning "  (Does the account exist on this server?)"
        }
    }

    # Ensure the user is a member of the lab group
    $alreadyMember = Get-LocalGroupMember -Group $LabGroup -ErrorAction SilentlyContinue |
                     Where-Object { $_.Name -eq $account }
    if (-not $alreadyMember) {
        if ($DryRun) {
            Write-Host "           [DRY RUN] Would add $user to $LabGroup" -ForegroundColor Magenta
        } else {
            try {
                Add-LocalGroupMember -Group $LabGroup -Member $user -ErrorAction Stop
                Write-Host "           Added $user to $LabGroup"
            } catch {
                Write-Warning "  Could not add '$user' to group '$LabGroup': $_"
            }
        }
    } else {
        Write-Host "           $user is already in $LabGroup"
    }

    $userSw.Stop()
    Write-Host ("           [user done in {0:N1} s]" -f $userSw.Elapsed.TotalSeconds) -ForegroundColor DarkGray
    $status.CompletedUsers += $user
    $status.CurrentUser = $null
}

$completed = $true

}
finally {
    $overallSw.Stop()
    Write-Host ""
    if ($completed) {
        if ($DryRun) {
            Write-Host "=== Dry run complete -- NO changes were made ===" -ForegroundColor Magenta
        } else {
            Write-Host "=== Done ===" -ForegroundColor Green
        }
    } else {
        if ($DryRun) {
            Write-Host "=== Dry run exited early -- progress summary below (no changes were made) ===" -ForegroundColor Magenta
        } else {
            Write-Host "=== Script exited early -- progress summary below ===" -ForegroundColor Red
        }
    }
    Write-Host ("  [{0}] Lab group '$LabGroup' ensured"        -f $(if ($status.LabGroupOk) {'X'} else {' '}))
    Write-Host ("  [{0}] Reader account '$ReaderUser' ensured" -f $(if ($status.ReaderOk)   {'X'} else {' '}))
    Write-Host ("  [{0}] Root ACL configured on $Root"         -f $(if ($status.RootAclOk)  {'X'} else {' '}))
    Write-Host ("  Users fully processed: {0} / {1}" -f $status.CompletedUsers.Count, $Users.Count)
    if ($status.CompletedUsers.Count -gt 0) {
        Write-Host ("       completed:          {0}" -f ($status.CompletedUsers -join ', ')) -ForegroundColor Green
    }
    if ($status.CurrentUser) {
        Write-Host ("       interrupted during: {0}" -f $status.CurrentUser) -ForegroundColor Yellow
    }
    $notStarted = @($Users | Where-Object { ($status.CompletedUsers -notcontains $_) -and ($_ -ne $status.CurrentUser) })
    if ($notStarted.Count -gt 0) {
        Write-Host ("       not started:        {0}" -f ($notStarted -join ', '))
    }
    Write-Host ""
    Write-Host ("Total elapsed: {0:N1} min" -f $overallSw.Elapsed.TotalMinutes)
    if (-not $completed) {
        Write-Host ""
        Write-Host "The script is idempotent -- re-running with the same arguments will" -ForegroundColor DarkGray
        Write-Host "pick up where it left off (completed steps become no-ops)." -ForegroundColor DarkGray
    }
}
