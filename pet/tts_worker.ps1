param(
    [string]$JobFile,
    [string]$ResultFile,
    [switch]$Session
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Set-JobVoice($speaker, $job) {
    $voices = $speaker.GetVoices()
    $picked = $null
    $wanted = [string]$job.voice
    $languageId = if ($job.language -eq 'it') { 16 } else { 9 }
    for ($i = 0; $i -lt $voices.Count; $i++) {
        $candidate = $voices.Item($i)
        $matchesLanguage = $false
        foreach ($lang in ($candidate.GetAttribute('Language') -split ';')) {
            if (([Convert]::ToInt32($lang, 16) -band 0x3ff) -eq $languageId) { $matchesLanguage = $true; break }
        }
        if (-not $matchesLanguage) { continue }
        if (-not $picked) { $picked = $candidate }
        if ($wanted -and $candidate.GetDescription() -match [regex]::Escape($wanted)) {
            $picked = $candidate
            break
        }
    }
    if (-not $picked) { throw 'No SAPI voice installed for the selected language' }
    $speaker.Voice = $picked
    $speaker.Rate = [int]$job.rate
    $speaker.Volume = 100
}

function Write-VoiceFile($speaker, [string]$target, [string]$text) {
    $stream = New-Object -ComObject SAPI.SpFileStream
    $stream.Open($target, 3, $false)
    try {
        $speaker.AudioOutputStream = $stream
        $speaker.Speak($text, 16) | Out-Null
    } finally {
        $stream.Close()
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($stream)
    }
}

if ($Session) {
    $voice = New-Object -ComObject SAPI.SpVoice
    $selected = $false
    while ($null -ne ($line = [Console]::ReadLine())) {
        $job = $line | ConvertFrom-Json
        if (-not $selected) {
            Set-JobVoice $voice $job
            $selected = $true
        }
        $pending = $job.path + '.pending'
        Write-VoiceFile $voice $pending $job.text
        [System.IO.File]::Move($pending, $job.path)
    }
    exit 0
}

$result = [ordered]@{ ok = $false; error = ''; items = @() }
try {
    $jobText = [System.IO.File]::ReadAllText($JobFile, [System.Text.Encoding]::UTF8)
    $job = $jobText | ConvertFrom-Json
    $voice = New-Object -ComObject SAPI.SpVoice
    Set-JobVoice $voice $job
    $items = @()
    foreach ($item in $job.items) {
        $entry = [ordered]@{ path = [string]$item.path; ok = $false; error = '' }
        try {
            Write-VoiceFile $voice $item.path $item.text
            $entry.ok = $true
        } catch {
            $entry.error = $_.Exception.Message
        }
        $items += $entry
    }
    $result.ok = $true
    $result.items = @($items)
} catch {
    $result.error = $_.Exception.Message
}

$json = $result | ConvertTo-Json -Depth 6 -Compress
[System.IO.File]::WriteAllText($ResultFile, $json, (New-Object System.Text.UTF8Encoding($false)))
exit 0
