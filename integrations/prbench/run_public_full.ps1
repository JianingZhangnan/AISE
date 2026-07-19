param(
    [Parameter(Mandatory=$true)][string]$EvaluatorRoot,
    [Parameter(Mandatory=$true)][string]$WheelPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($env:PHYCODE_API_KEY) -or
    [string]::IsNullOrWhiteSpace($env:PHYCODE_BASE_URL) -or
    [string]::IsNullOrWhiteSpace($env:PHYCODE_MODEL)) {
    throw 'PHYCODE_API_KEY, PHYCODE_BASE_URL, and PHYCODE_MODEL must be configured in the current process.'
}
Write-Host 'PhyCode provider environment configured (values are not displayed).'

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$EvaluatorRoot = (Resolve-Path -LiteralPath $EvaluatorRoot).Path
$WheelPath = (Resolve-Path -LiteralPath $WheelPath).Path
$AdapterPath = Join-Path $PSScriptRoot 'apply_adapter.py'
$ContractPath = Join-Path $PSScriptRoot 'public_contracts\task_white_1993.json'
if (-not (Test-Path -LiteralPath $ContractPath -PathType Leaf)) {
    throw 'Public contract for task_white_1993 is unavailable.'
}

$approvalGrants = @(
    @{ tool_name = 'file.write'; path = 'reproduction/ANALYSIS.md' }
    @{ tool_name = 'file.edit'; path = 'reproduction/ANALYSIS.md' }
    @{ tool_name = 'file.edit'; path = 'reproduction/ANALYSIS.md' }
    @{ tool_name = 'file.write'; path = 'reproduction/operators.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/operators.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/operators.py' }
    @{ tool_name = 'file.write'; path = 'reproduction/block.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/block.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/block.py' }
    @{ tool_name = 'file.write'; path = 'reproduction/superblock.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/superblock.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/superblock.py' }
    @{ tool_name = 'file.write'; path = 'reproduction/dmrg_infinite.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/dmrg_infinite.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/dmrg_infinite.py' }
    @{ tool_name = 'file.write'; path = 'reproduction/dmrg_finite.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/dmrg_finite.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/dmrg_finite.py' }
    @{ tool_name = 'file.write'; path = 'reproduction/fig2_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig2_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig2_compute.py' }
    @{ tool_name = 'file.write'; path = 'reproduction/fig3_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig3_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig3_compute.py' }
    @{ tool_name = 'file.write'; path = 'reproduction/fig4_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig4_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig4_compute.py' }
    @{ tool_name = 'file.write'; path = 'reproduction/fig5_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig5_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig5_compute.py' }
    @{ tool_name = 'file.write'; path = 'reproduction/fig6_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig6_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig6_compute.py' }
    @{ tool_name = 'file.write'; path = 'reproduction/fig7_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig7_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig7_compute.py' }
    @{ tool_name = 'file.write'; path = 'reproduction/fig8_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig8_compute.py' }
    @{ tool_name = 'file.edit'; path = 'reproduction/fig8_compute.py' }
)

$approvalRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'phycode-prbench-full-approvals-' + [guid]::NewGuid().ToString('N')
)
[void](New-Item -ItemType Directory -Path $approvalRoot)
$ApprovalPath = Join-Path $approvalRoot 'task_white_1993.json'
$ApprovalJson = @{ grants = $approvalGrants } | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText(
    $ApprovalPath,
    $ApprovalJson + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)

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

    Write-Host 'Starting the official PRBench evaluator for task_white_1993.'
    Push-Location $EvaluatorRoot
    try {
        $env:OPENCODE_API_KEY = $env:PHYCODE_API_KEY
        $env:OPENCODE_BASE_URL = $env:PHYCODE_BASE_URL
        $env:OPENCODE_MODEL = 'openai/' + $env:PHYCODE_MODEL
        & uv run --with 'a2a-sdk[http-server]==0.3.8' python main.py launch `
            --task-id task_white_1993 `
            --white-agent-type phycode `
            --green-agent-type opencode `
            --phycode-contract $ContractPath `
            --phycode-approvals $ApprovalPath `
            --approval-wait-seconds 900 `
            --phycode-max-tool-calls 50 `
            --phycode-max-context-chars 24000
        if ($LASTEXITCODE -ne 0) {
            throw 'Official PRBench evaluator failed for task_white_1993.'
        }
    }
    finally {
        Restore-OpenCodeEnvironment
        Pop-Location
    }
}
finally {
    Restore-OpenCodeEnvironment
    if (Test-Path -LiteralPath $approvalRoot) {
        Remove-Item -LiteralPath $approvalRoot -Recurse -Force
    }
}
