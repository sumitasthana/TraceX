# PowerShell deployment script for Contextual Data Discovery to S3
# Usage: .\deploy.ps1 -BucketName "your-bucket-name" -Region "us-east-1"

param(
    [string]$BucketName = "contextual-data-discovery-demo",
    [string]$Region = "us-east-1",
    [string]$LambdaFunctionName = "anthropic-api-proxy"
)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Deploying Contextual Data Discovery" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Bucket: $BucketName"
Write-Host "Region: $Region"
Write-Host ""

# Check if AWS CLI is installed
try {
    aws --version | Out-Null
} catch {
    Write-Host "Error: AWS CLI is not installed" -ForegroundColor Red
    exit 1
}

# Check if bucket exists
$bucketExists = $false
try {
    aws s3 ls "s3://$BucketName" 2>&1 | Out-Null
    $bucketExists = $LASTEXITCODE -eq 0
} catch {
    $bucketExists = $false
}

if (-not $bucketExists) {
    Write-Host "Creating S3 bucket..." -ForegroundColor Yellow
    aws s3 mb "s3://$BucketName" --region $Region
    
    Write-Host "Configuring static website hosting..." -ForegroundColor Yellow
    aws s3 website "s3://$BucketName" `
        --index-document prototype_v2.html `
        --error-document prototype_v2.html
    
    Write-Host "Disabling block public access..." -ForegroundColor Yellow
    aws s3api put-public-access-block `
        --bucket $BucketName `
        --public-access-block-configuration `
        "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"
    
    Write-Host "Applying bucket policy..." -ForegroundColor Yellow
    # Update bucket policy with actual bucket name
    $policyContent = Get-Content bucket-policy.json -Raw
    $policyContent = $policyContent -replace "contextual-data-discovery-demo", $BucketName
    $policyContent | Out-File -FilePath "$env:TEMP\bucket-policy.json" -Encoding utf8
    
    aws s3api put-bucket-policy `
        --bucket $BucketName `
        --policy "file:///$env:TEMP\bucket-policy.json"
    
    Remove-Item "$env:TEMP\bucket-policy.json"
} else {
    Write-Host "Bucket already exists, skipping creation..." -ForegroundColor Green
}

# Upload HTML file
Write-Host ""
Write-Host "Uploading prototype_v2.html..." -ForegroundColor Yellow
aws s3 cp prototype_v2.html "s3://$BucketName/" `
    --content-type "text/html" `
    --cache-control "max-age=300"

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "Deployment Complete!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Website URL:" -ForegroundColor Cyan
Write-Host "http://$BucketName.s3-website-$Region.amazonaws.com" -ForegroundColor White
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Ensure Lambda function '$LambdaFunctionName' is deployed"
Write-Host "2. Verify ANTHROPIC_API_KEY environment variable is set in Lambda"
Write-Host "3. Update the Lambda URL in prototype_v2.html if needed"
Write-Host ""
Write-Host "To update the site, run:" -ForegroundColor Cyan
Write-Host "aws s3 cp prototype_v2.html s3://$BucketName/ --content-type `"text/html`"" -ForegroundColor White
Write-Host ""
