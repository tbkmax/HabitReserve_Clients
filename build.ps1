param(
    [string]$ServerUrl = "",
    [string]$Version = "",
    [string]$ReleaseRepository = "",
    [switch]$Release
)

$ErrorActionPreference = "Stop"
$DesktopRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartingLocation = (Get-Location).Path

try {
Set-Location -LiteralPath $DesktopRoot

function Assert-LastCommandSucceeded {
    param([string]$Description)
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

$VirtualEnvironmentPython = Join-Path $DesktopRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VirtualEnvironmentPython)) {
    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    $PythonArguments = @()
    if ($null -eq $PythonCommand) {
        $PythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
        $PythonArguments = @("-3.12")
    }
    if ($null -eq $PythonCommand) {
        throw "Python 3.12 or newer is required. Install it before building the desktop client."
    }
    & $PythonCommand.Source @PythonArguments -m venv (Join-Path $DesktopRoot ".venv")
    Assert-LastCommandSucceeded "Creating the desktop Python virtual environment"
}
$Python = $VirtualEnvironmentPython
& $Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "The desktop virtual environment must use Python 3.12 or newer. Delete desktop/.venv and run the build again after installing a supported Python."
}

if ($Release -and [string]::IsNullOrWhiteSpace($ServerUrl)) {
    throw "A release build requires -ServerUrl https://your-production-domain."
}
if ($ReleaseRepository -and $ReleaseRepository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "ReleaseRepository must use the public desktop repository's owner/name."
}
if ($Release -and -not $ReleaseRepository) {
    throw "A release build requires -ReleaseRepository owner/name."
}
$VersionSource = Get-Content -LiteralPath (Join-Path $DesktopRoot "src\time_bank_tracker\__init__.py") -Raw
$VersionMatch = [regex]::Match($VersionSource, '(?m)^__version__ = "([^"\r\n]+)"\r?$')
if (-not $VersionMatch.Success) {
    throw "Desktop package version was not found in __init__.py."
}
$SourceVersion = $VersionMatch.Groups[1].Value
if (-not [string]::IsNullOrWhiteSpace($Version) -and $Version -ne $SourceVersion) {
    throw "Requested version $Version does not match the desktop package version $SourceVersion."
}
$Version = $SourceVersion
if ($Version -notmatch '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$') {
    throw "Desktop version must use major.minor.patch numbers, such as 1.0.0."
}

if ([string]::IsNullOrWhiteSpace($ServerUrl)) {
    $ServerUrl = "https://localhost"
}
$ParsedServerUrl = $null
if (-not [Uri]::TryCreate($ServerUrl, [UriKind]::Absolute, [ref]$ParsedServerUrl) -or
    $ParsedServerUrl.Scheme -ne "https" -or
    [string]::IsNullOrWhiteSpace($ParsedServerUrl.Host) -or
    -not [string]::IsNullOrEmpty($ParsedServerUrl.UserInfo) -or
    -not [string]::IsNullOrEmpty($ParsedServerUrl.Query) -or
    -not [string]::IsNullOrEmpty($ParsedServerUrl.Fragment) -or
    $ParsedServerUrl.AbsolutePath -ne "/") {
    throw "ServerUrl must be an HTTPS origin only, for example https://time.example.com."
}
$ServerUrl = $ParsedServerUrl.GetLeftPart([UriPartial]::Authority)
if ($Release -and $ParsedServerUrl.Host -in @("localhost", "127.0.0.1", "::1")) {
    throw "A release build cannot use a localhost server URL."
}

$BuildConfigDirectory = Join-Path ([IO.Path]::GetTempPath()) ("habitreserve-build-" + [Guid]::NewGuid().ToString("N"))
$BuildConfigPath = Join-Path $BuildConfigDirectory "_habitreserve_build.json"
$PreviousBuildConfig = $env:HABITRESERVE_BUILD_CONFIG
$VersionResourcePath = Join-Path $BuildConfigDirectory "version_info.txt"
$PreviousVersionResource = $env:HABITRESERVE_VERSION_RESOURCE

try {
    New-Item -ItemType Directory -Path $BuildConfigDirectory | Out-Null
    & $Python .\make_version_resource.py --version $Version --output $VersionResourcePath
    Assert-LastCommandSucceeded "Creating Windows version metadata"
    $env:HABITRESERVE_VERSION_RESOURCE = $VersionResourcePath
    $BuildConfigJson = @{
        server_url = $ServerUrl
        version = $Version
        release = [bool]$Release
        release_repository = $ReleaseRepository
    } | ConvertTo-Json
    [IO.File]::WriteAllText($BuildConfigPath, $BuildConfigJson, [Text.UTF8Encoding]::new($false))
    $env:HABITRESERVE_BUILD_CONFIG = $BuildConfigPath

    & $Python -m pip install -e ".[dev]"
    Assert-LastCommandSucceeded "Installing desktop build dependencies"
    & $Python -m pytest
    Assert-LastCommandSucceeded "Desktop tests"
    & $Python -m PyInstaller --clean --noconfirm HabitReserve.spec
    Assert-LastCommandSucceeded "PyInstaller"

    $PortableSource = Join-Path $DesktopRoot "dist\HabitReserve.exe"

    if ($Release) {
        $ArtifactDirectory = Join-Path $DesktopRoot "release"
        New-Item -ItemType Directory -Path $ArtifactDirectory -Force | Out-Null
        $PortableArtifact = Join-Path $ArtifactDirectory "HabitReserve-$Version.exe"
        Copy-Item -LiteralPath $PortableSource -Destination $PortableArtifact -Force
        $ZipArtifact = Join-Path $ArtifactDirectory "HabitReserve-$Version.zip"
        if (Test-Path -LiteralPath $ZipArtifact) { Remove-Item -LiteralPath $ZipArtifact -Force }
        Compress-Archive -LiteralPath $PortableSource -DestinationPath $ZipArtifact -CompressionLevel Optimal

        Write-Host "Built release artifacts in $ArtifactDirectory"
        Write-Host "Embedded server origin: $ServerUrl"
    } else {
        Write-Host "Built portable app $PortableSource"
    }
} finally {
    $env:HABITRESERVE_BUILD_CONFIG = $PreviousBuildConfig
    $env:HABITRESERVE_VERSION_RESOURCE = $PreviousVersionResource
    if (Test-Path -LiteralPath $BuildConfigDirectory) {
        Remove-Item -LiteralPath $BuildConfigDirectory -Recurse -Force
    }
}
} finally {
    Set-Location -LiteralPath $StartingLocation
}
