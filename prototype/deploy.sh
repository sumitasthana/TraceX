#!/bin/bash

# Deployment script for Contextual Data Discovery to S3
# Usage: ./deploy.sh [bucket-name] [region]

set -e

BUCKET_NAME=${1:-contextual-data-discovery-demo}
REGION=${2:-us-east-1}
LAMBDA_FUNCTION_NAME="anthropic-api-proxy"

echo "=========================================="
echo "Deploying Contextual Data Discovery"
echo "=========================================="
echo "Bucket: $BUCKET_NAME"
echo "Region: $REGION"
echo ""

# Check if AWS CLI is installed
if ! command -v aws &> /dev/null; then
    echo "Error: AWS CLI is not installed"
    exit 1
fi

# Check if bucket exists
if aws s3 ls "s3://$BUCKET_NAME" 2>&1 | grep -q 'NoSuchBucket'; then
    echo "Creating S3 bucket..."
    aws s3 mb "s3://$BUCKET_NAME" --region "$REGION"
    
    echo "Configuring static website hosting..."
    aws s3 website "s3://$BUCKET_NAME" \
        --index-document prototype_v2.html \
        --error-document prototype_v2.html
    
    echo "Disabling block public access..."
    aws s3api put-public-access-block \
        --bucket "$BUCKET_NAME" \
        --public-access-block-configuration \
        BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false
    
    echo "Applying bucket policy..."
    # Update bucket policy with actual bucket name
    sed "s/contextual-data-discovery-demo/$BUCKET_NAME/g" bucket-policy.json > /tmp/bucket-policy.json
    aws s3api put-bucket-policy \
        --bucket "$BUCKET_NAME" \
        --policy file:///tmp/bucket-policy.json
    rm /tmp/bucket-policy.json
else
    echo "Bucket already exists, skipping creation..."
fi

# Upload HTML file
echo ""
echo "Uploading prototype_v2.html..."
aws s3 cp prototype_v2.html "s3://$BUCKET_NAME/" \
    --content-type "text/html" \
    --cache-control "max-age=300"

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "Website URL:"
echo "http://$BUCKET_NAME.s3-website-$REGION.amazonaws.com"
echo ""
echo "Next steps:"
echo "1. Ensure Lambda function '$LAMBDA_FUNCTION_NAME' is deployed"
echo "2. Verify ANTHROPIC_API_KEY environment variable is set in Lambda"
echo "3. Update the Lambda URL in prototype_v2.html if needed"
echo ""
echo "To update the site, run:"
echo "aws s3 cp prototype_v2.html s3://$BUCKET_NAME/ --content-type \"text/html\""
echo ""
