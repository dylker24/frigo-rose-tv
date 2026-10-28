# Test de l'appli Studio sur un vrai Windows : préparation des outils, YouTube, conversion
$ErrorActionPreference = "Continue"
$env:FRS_NO_BROWSER = "1"
$env:LOCALAPPDATA = Join-Path $env:RUNNER_TEMP "appdata"
New-Item -ItemType Directory -Force -Path $env:LOCALAPPDATA | Out-Null
$out = Join-Path $env:RUNNER_TEMP "sortie"; New-Item -ItemType Directory -Force -Path $out | Out-Null
$log = @()
$p = Start-Process -FilePath "dist\FrigoRoseStudio.exe" -PassThru
$api = "http://127.0.0.1:8795"
function St { try { Invoke-RestMethod "$api/api/state" -TimeoutSec 5 } catch { $null } }
$t0 = Get-Date; $s = $null
while (((Get-Date) - $t0).TotalSeconds -lt 300) { $s = St; if ($s -and ($s.setup.ready -or $s.setup.error)) { break }; Start-Sleep 3 }
$log += "Préparation : ready=$($s.setup.ready) error=$($s.setup.error) en $([int]((Get-Date)-$t0).TotalSeconds) s, nvenc=$($s.setup.nvenc)"
Start-Sleep 8; $s = St; $log += "yt-dlp : $($s.setup.ytdlp_version)"
$bin = Join-Path $env:LOCALAPPDATA "FrigoRoseStudio\bin"
$log += "Outils : " + ((Get-ChildItem $bin | ForEach-Object { "$($_.Name) $([int]($_.Length/1MB)) Mo" }) -join ", ")
function Post($path, $obj) { Invoke-RestMethod "$api$path" -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress))) }
try { Post "/api/download" @{ url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"; dest = $out; quality = "1080" } | Out-Null } catch { $log += "download KO $_" }
& "$bin\ffmpeg.exe" -v error -y -f lavfi -i "testsrc2=s=640x360:r=25:d=5" -f lavfi -i "sine=d=5" -ac 6 -c:v mpeg4 -vtag XVID -c:a ac3 (Join-Path $out "Vieux film é.avi")
try { Post "/api/convert" @{ files = @((Join-Path $out "Vieux film é.avi")); dest = "" } | Out-Null } catch { $log += "convert KO $_" }
$t0 = Get-Date
while (((Get-Date) - $t0).TotalSeconds -lt 240) { $s = St; if ($s -and -not ($s.jobs | Where-Object { $_.status -in @("attente","en cours") })) { break }; Start-Sleep 3 }
foreach ($j in $s.jobs) { $log += "- [$($j.kind)] $($j.status) | $($j.title) | $($j.msg) | $($j.out -join ', ')" }
$log += "Fichiers : " + ((Get-ChildItem $out -Recurse | ForEach-Object { "$($_.Name) ($([int]($_.Length/1KB)) ko)" }) -join " ; ")
$mp4 = Get-ChildItem $out -Filter *.mp4 -Recurse | Select-Object -First 3
foreach ($f in $mp4) { $log += "$($f.Name) : " + ((& "$bin\ffprobe.exe" -v error -show_entries stream=codec_name,width,height,channels -of csv=p=0 $f.FullName) -join " / ") }
try { $r = Post "/api/autostart" @{ on = $true }; $v = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name FrigoRoseStudio -ErrorAction SilentlyContinue).FrigoRoseStudio; $log += "Démarrage auto : on=$($r.on) valeur=$v"; $r = Post "/api/autostart" @{ on = $false }; $log += "Démarrage auto retiré : on=$($r.on)" } catch { $log += "autostart KO $_" }
$lot = Join-Path $out "lot\Saison 1"; New-Item -ItemType Directory -Force -Path $lot | Out-Null
foreach ($n in 1..3) { & "$bin\ffmpeg.exe" -v error -y -f lavfi -i "testsrc2=s=1280x720:r=25:d=15" -f lavfi -i "sine=d=15" -c:v mpeg4 -c:a ac3 (Join-Path $lot "E0$n.avi") }
try { $r = Post "/api/convert_dir" @{ path = (Join-Path $out "lot"); dest = "" }; $log += "Dossier entier : $($r.added) ajoutées, $($r.skipped) sautées" } catch { $log += "convert_dir KO $_" }
$t0 = Get-Date; $maxp = 0
while (((Get-Date) - $t0).TotalSeconds -lt 240) { $s = St; $c = @($s.jobs | Where-Object { $_.status -eq "en cours" }).Count; if ($c -gt $maxp) { $maxp = $c }; if ($s -and -not ($s.jobs | Where-Object { $_.status -in @("attente","en cours") })) { break }; Start-Sleep 1 }
$log += "Dossier entier : max $maxp en même temps, " + (($s.jobs | Where-Object { $_.kind -eq "conv" } | ForEach-Object { "$($_.title)=$($_.status)" }) -join ", ")
$ts = (Invoke-RestMethod "$api/api/state").conf.tunarr; $log += "Tunarr vu par Studio : ok=$($ts.ok)"
try { $r = Invoke-RestMethod "$api/api/yt?q=mickey" -TimeoutSec 90; $log += "Recherche YouTube : $($r.results.Count) résultats, 1er = $($r.results[0].title)" } catch { $log += "Recherche YouTube KO : $($_.ErrorDetails.Message)" }
try { $r = Post "/api/pin" @{ pin = "2424" }; $log += "Code : ok" } catch { $log += "Code KO $_" }
try { $ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" } | Select-Object -First 1).IPAddress; $c = Invoke-WebRequest "http://$($ip):8795/api/state" -UseBasicParsing -TimeoutSec 5; $log += "Accès réseau local ($ip) : $($c.StatusCode) (devrait être refusé)" } catch { $log += "Accès réseau local refusé ✓ ($($_.Exception.Message.Substring(0,[Math]::Min(80,$_.Exception.Message.Length))))" }
try { Post "/api/quit" @{} | Out-Null } catch {}
Start-Sleep 2; if (-not $p.HasExited) { $log += "ATTENTION : l'appli ne s'est pas fermée"; Stop-Process $p -Force }
($log -join "`n") | Out-File -Encoding utf8 smoke.md
Get-Content smoke.md
