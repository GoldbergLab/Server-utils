<#
.SYNOPSIS
    Inspect the ACL on a file or folder in a form that's easier to read
    than the default Get-Acl output.

.DESCRIPTION
    Prints:
      - Owner (flags orphaned SIDs that no longer resolve to a name)
      - Whether inheritance is enabled ("protected" = disabled)
      - Each Access Control Entry (ACE): inherited vs. explicit, identity,
        rights, and inheritance/propagation flags

    Useful for sanity-checking the permission state of an existing share
    before (or after) running setup_lab_userfolders.ps1. Run it on a few
    items at varying depths (root folder, a user folder, a file deep inside)
    to get a sense of whether the tree uses inheritance consistently or has
    a lot of stale explicit ACEs from previous accounts/servers.

.PARAMETER Path
    The file or folder to inspect.

.EXAMPLE
    .\inspect_acl.ps1 -Path "Z:\"
    .\inspect_acl.ps1 -Path "Z:\someuser"
    .\inspect_acl.ps1 -Path "Z:\someuser\deep\subfolder\file.dat"
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$Path
)

if (-not (Test-Path $Path)) {
    throw "Path does not exist: $Path"
}

$acl = Get-Acl $Path

Write-Host ""
Write-Host "Path:  $Path" -ForegroundColor Cyan
Write-Host ("Owner: {0}" -f $acl.Owner)
if ($acl.Owner -match '^S-1-') {
    Write-Host "       ^ raw SID -- the owning account no longer exists on this server (orphaned)." -ForegroundColor Yellow
}
Write-Host ""

if ($acl.AreAccessRulesProtected) {
    Write-Host "Inheritance: DISABLED  (this item has its OWN ACL and does NOT inherit from its parent)" -ForegroundColor Yellow
} else {
    Write-Host "Inheritance: enabled   (this item inherits ACEs from its parent)" -ForegroundColor Green
}
Write-Host ""

Write-Host "Access Control Entries (ACEs):"
Write-Host ""
$i = 0
foreach ($ace in $acl.Access) {
    $i++
    $source = if ($ace.IsInherited) { "inherited" } else { "EXPLICIT " }
    $identity = $ace.IdentityReference.Value
    $orphaned = $identity -match '^S-1-'
    $rights = $ace.FileSystemRights.ToString()
    $allowDeny = $ace.AccessControlType.ToString()
    $flags = "Inh:$($ace.InheritanceFlags) Prop:$($ace.PropagationFlags)"
    $color = if ($orphaned) { "Yellow" } elseif ($ace.IsInherited) { "Gray" } else { "White" }

    Write-Host ("  [{0}] {1} {2}  {3}" -f $i, $source, $allowDeny, $identity) -ForegroundColor $color
    Write-Host ("       rights: {0}" -f $rights) -ForegroundColor $color
    Write-Host ("       {0}" -f $flags) -ForegroundColor DarkGray
    if ($orphaned) {
        Write-Host "       ^ orphaned SID -- account no longer exists on this server" -ForegroundColor Yellow
    }
    Write-Host ""
}

Write-Host "How to read this:"
Write-Host "  - 'inherited' ACEs come from a parent folder. Re-running setup_lab_userfolders.ps1"
Write-Host "    and changing the root ACL WILL update these automatically via propagation."
Write-Host "  - 'EXPLICIT' ACEs are set directly on this item. The setup script only clears"
Write-Host "    explicit ACEs on the top-level user folders themselves -- explicit ACEs on"
Write-Host "    items DEEP INSIDE a user folder are NOT touched by the script."
Write-Host "  - Orphaned SIDs (yellow) are ACEs for accounts that no longer exist on this"
Write-Host "    server. They're functionally harmless (the SID matches nobody), but clutter"
Write-Host "    the ACL. They can be removed manually with icacls if desired."
Write-Host ""
Write-Host "Sanity-check strategy: inspect the root, a user folder, and a few files deep"
Write-Host "inside different user folders. If nearly everything shows only 'inherited'"
Write-Host "ACEs with no orphaned SIDs, a re-run of setup_lab_userfolders.ps1 will leave"
Write-Host "the tree clean. If there are many EXPLICIT or orphaned ACEs at depth, you may"
Write-Host "want to run 'icacls <path> /reset /T /C' afterward to reset child ACLs to"
Write-Host "inherit-only (do this carefully and only once the root ACL is correct)."
