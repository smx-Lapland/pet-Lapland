param(
    [Parameter(Mandatory = $true)][string]$JobFile,
    [Parameter(Mandatory = $true)][string]$ResultFile
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$result = [ordered]@{ ok = $false; error = ''; items = @() }

try {
    $jobText = [System.IO.File]::ReadAllText($JobFile, [System.Text.Encoding]::UTF8)
    $job = $jobText | ConvertFrom-Json

    $voice = New-Object -ComObject SAPI.SpVoice
    $voices = $voice.GetVoices()
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
    $voice.Voice = $picked
    $voice.Rate = [int]$job.rate
    $voice.Volume = 100

    $items = @()
    foreach ($item in $job.items) {
        $entry = [ordered]@{ path = [string]$item.path; ok = $false; error = '' }
        try {
            $target = [string]$item.path
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
            $stream = New-Object -ComObject SAPI.SpFileStream
            $stream.Open($target, 3, $false)
            $voice.AudioOutputStream = $stream
            $voice.Speak([string]$item.text, 16) | Out-Null
            $stream.Close()
            [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($stream)
            $entry.ok = $true
        }
        catch {
            $entry.error = $_.Exception.Message
        }
        $items += $entry
    }

    $result.ok = $true
    $result.items = @($items)
}
catch {
    $result.ok = $false
    $result.error = $_.Exception.Message
}

$json = $result | ConvertTo-Json -Depth 6 -Compress
[System.IO.File]::WriteAllText($ResultFile, $json, (New-Object System.Text.UTF8Encoding($false)))
exit 0
