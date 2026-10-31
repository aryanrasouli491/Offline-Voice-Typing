# Copyright (c) 2026 Nooraqa Rasouli. SPDX-License-Identifier: MIT
# Generate an offline English speech fixture using the voice built into Windows.
$fixtureFolder = Join-Path $PSScriptRoot 'fixtures'
New-Item -ItemType Directory -Force -Path $fixtureFolder | Out-Null
$fixtureSpeaker = New-Object -ComObject SAPI.SpVoice
$fixtureStream = New-Object -ComObject SAPI.SpFileStream
try {
    $fixtureStream.Open((Join-Path $fixtureFolder 'english-fixture.wav'), 3, $false)
    $fixtureSpeaker.AudioOutputStream = $fixtureStream
    $fixtureSpeaker.Speak('Hello world. This is a test of offline voice typing. Please write these words in the text box.') | Out-Null
} finally {
    $fixtureStream.Close()
}
