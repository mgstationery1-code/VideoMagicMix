param([string]$Output = 'build/smoke-voice.wav')
$ErrorActionPreference = 'Stop'
$voicePath = [IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $voicePath) | Out-Null
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $speaker.Rate = -2
    $speaker.SetOutputToWaveFile($voicePath)
    $speaker.Speak('This is a test of the local voice recognition system. The software can create videos on this computer.')
} finally { $speaker.Dispose() }
Write-Host "Generated synthetic test voice: $voicePath"
