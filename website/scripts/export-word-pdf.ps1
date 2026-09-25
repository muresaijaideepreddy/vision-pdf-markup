param([Parameter(Mandatory=$true)][string]$InputPath,[Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference='Stop'
$word=$null
$document=$null
$savedOptions=$null
$ownedWordProcess=$null
# InputPath is the adapter's disposable copy, never the uploaded/original file.
# Lock existing cached field results before Word opens it (DATE can update on open).
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression
$archive=[IO.Compression.ZipFile]::Open($InputPath,[IO.Compression.ZipArchiveMode]::Update)
try {
  $partNames=@($archive.Entries | Where-Object {$_.FullName -match '^word/.*\.xml$'} | ForEach-Object {$_.FullName})
  foreach($partName in $partNames){
    $entry=$archive.GetEntry($partName)
    $reader=New-Object IO.StreamReader($entry.Open())
    try{$xmlText=$reader.ReadToEnd()}finally{$reader.Dispose()}
    $xml=New-Object Xml.XmlDocument
    $xml.PreserveWhitespace=$true
    $xml.XmlResolver=$null
    $xml.LoadXml($xmlText)
    $ns=New-Object Xml.XmlNamespaceManager($xml.NameTable)
    $wordNamespace='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    $ns.AddNamespace('w',$wordNamespace)
    $fields=$xml.SelectNodes('//w:fldChar[@w:fldCharType="begin"] | //w:fldSimple',$ns)
    if($fields.Count -eq 0){continue}
    foreach($field in $fields){$field.SetAttribute('fldLock',$wordNamespace,'true') | Out-Null}
    $entry.Delete()
    $newEntry=$archive.CreateEntry($partName)
    $writer=New-Object IO.StreamWriter($newEntry.Open(),(New-Object Text.UTF8Encoding($false)))
    try{$writer.Write($xml.OuterXml)}finally{$writer.Dispose()}
  }
}finally{$archive.Dispose()}
try {
  $existingWordIds=@(Get-Process WINWORD -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
  $word=New-Object -ComObject Word.Application
  $word.Visible=$false
  $word.DisplayAlerts=0
  $word.AutomationSecurity=3
  $savedOptions=@($word.Options.UpdateLinksAtOpen,$word.Options.UpdateFieldsAtPrint,$word.Options.UpdateLinksAtPrint)
  $word.Options.UpdateLinksAtOpen=$false
  $word.Options.UpdateFieldsAtPrint=$false
  $word.Options.UpdateLinksAtPrint=$false
  # Obtain the PID through a blank document before opening user content.
  $document=$word.Documents.Add([Type]::Missing,$false,0,$false)
  Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class WordWindowProcess { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId); }'
  [uint32]$wordProcessId=0
  [void][WordWindowProcess]::GetWindowThreadProcessId([IntPtr]$document.ActiveWindow.Hwnd,[ref]$wordProcessId)
  if($wordProcessId -gt 0 -and $existingWordIds -notcontains $wordProcessId){$ownedWordProcess=Get-Process -Id $wordProcessId;Write-Output "WORD_PROCESS_ID=$wordProcessId"}
  $document.Close(0)
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
  $document=$null
  $document=$word.Documents.Open($InputPath,$false,$true,$false,'','',$false,'','',0,0,$false)
  $document.ExportAsFixedFormat($OutputPath,17,$false,0,0,1,1,0,$false,$true,0,$true,$true,$false)
} finally {
  if($null -ne $document){try{$document.Close(0)}catch{};[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)}
  if($null -ne $word){if($null -ne $savedOptions){try{$word.Options.UpdateLinksAtOpen=$savedOptions[0];$word.Options.UpdateFieldsAtPrint=$savedOptions[1];$word.Options.UpdateLinksAtPrint=$savedOptions[2]}catch{}};try{$word.Quit(0)}catch{};[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)}
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
  # Some Office COM servers remain alive after Quit because of RCW references.
  # Terminate only the exact new instance whose window handle we recorded.
  if($null -ne $ownedWordProcess){try{if(-not $ownedWordProcess.WaitForExit(1500)){$ownedWordProcess.Kill()}}catch{}}
}
