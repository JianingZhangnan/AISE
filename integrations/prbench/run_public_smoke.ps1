param(
    [Parameter(Mandatory=$true)][string]$EvaluatorRoot,
    [Parameter(Mandatory=$true)][string]$WheelPath,
    [ValidateSet('aaatest_helloworld','bbbtest_alphabet')][string[]]$TaskIds
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($env:PHYCODE_API_KEY) -or
    [string]::IsNullOrWhiteSpace($env:PHYCODE_BASE_URL) -or
    [string]::IsNullOrWhiteSpace($env:PHYCODE_MODEL)) {
    throw 'PHYCODE_API_KEY, PHYCODE_BASE_URL, and PHYCODE_MODEL must be configured in the current process.'
}
Write-Host 'PhyCode provider environment configured: yes (values are not displayed).'

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$EvaluatorRoot = (Resolve-Path -LiteralPath $EvaluatorRoot).Path
$WheelPath = (Resolve-Path -LiteralPath $WheelPath).Path
$AdapterPath = Join-Path $PSScriptRoot 'apply_adapter.py'
$ContractRoot = Join-Path $PSScriptRoot 'public_contracts'

if (-not $TaskIds -or $TaskIds.Count -eq 0) {
    $TaskIds = @('aaatest_helloworld', 'bbbtest_alphabet')
}

$approvalGrants = @{
    aaatest_helloworld = @(
        @{ tool_name = 'file.write'; path = 'reproduction/hello.py' }
        @{ tool_name = 'file.write'; path = 'data/output.csv' }
    )
    bbbtest_alphabet = @(
        @{ tool_name = 'file.write'; path = 'reproduction/alphabet.py' }
        @{ tool_name = 'file.write'; path = 'data/letters.csv' }
    )
}

$approvalRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'phycode-prbench-approvals-' + [guid]::NewGuid().ToString('N')
)
[void](New-Item -ItemType Directory -Path $approvalRoot)
$openCodeNames = @('OPENCODE_API_KEY', 'OPENCODE_BASE_URL', 'OPENCODE_MODEL')
$previousOpenCodeEnvironment = @{}
foreach ($name in $openCodeNames) {
    $previousOpenCodeEnvironment[$name] = @{
        exists = Test-Path -LiteralPath ('Env:' + $name)
        value = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
}

function Restore-OpenCodeEnvironment {
    foreach ($name in $openCodeNames) {
        $saved = $previousOpenCodeEnvironment[$name]
        if ($saved.exists) {
            [Environment]::SetEnvironmentVariable($name, $saved.value, 'Process')
        }
        else {
            Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue
        }
    }
}

try {
    Push-Location $ProjectRoot
    try {
        & uv run python $AdapterPath $EvaluatorRoot $WheelPath
        if ($LASTEXITCODE -ne 0) {
            throw 'Pinned PRBench evaluator adapter failed.'
        }
    }
    finally {
        Pop-Location
    }

    foreach ($TaskId in $TaskIds) {
        $ContractPath = Join-Path $ContractRoot ($TaskId + '.json')
        if (-not (Test-Path -LiteralPath $ContractPath -PathType Leaf)) {
            throw "Public contract is unavailable for task $TaskId."
        }

        $ApprovalPath = Join-Path $approvalRoot ($TaskId + '.json')
        $ApprovalJson = @{ grants = $approvalGrants[$TaskId] } | ConvertTo-Json -Depth 6
        [System.IO.File]::WriteAllText(
            $ApprovalPath,
            $ApprovalJson + [Environment]::NewLine,
            [System.Text.UTF8Encoding]::new($false)
        )

        Write-Host "Starting official PRBench evaluator for public task $TaskId."
        Push-Location $EvaluatorRoot
        try {
            $env:OPENCODE_API_KEY = $env:PHYCODE_API_KEY
            $env:OPENCODE_BASE_URL = $env:PHYCODE_BASE_URL
            $env:OPENCODE_MODEL = 'openai/' + $env:PHYCODE_MODEL
            & uv run --with 'a2a-sdk[http-server]==0.3.8' python main.py launch `
                --task-id $TaskId `
                --white-agent-type phycode `
                --green-agent-type opencode `
                --phycode-contract $ContractPath `
                --phycode-approvals $ApprovalPath `
                --approval-wait-seconds 900
            if ($LASTEXITCODE -ne 0) {
                throw "Official PRBench evaluator failed for task $TaskId."
            }
        }
        finally {
            Restore-OpenCodeEnvironment
            Pop-Location
        }
    }
}
finally {
    Restore-OpenCodeEnvironment
    if (Test-Path -LiteralPath $approvalRoot) {
        Remove-Item -LiteralPath $approvalRoot -Recurse -Force
    }
}
