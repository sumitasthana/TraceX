# S3 Static Website Deployment Guide

This guide walks you through deploying the Contextual Data Discovery prototype to AWS S3 as a static website.

## Prerequisites

- AWS Account with appropriate permissions
- AWS CLI installed and configured
- Anthropic API key

## Architecture

```
User Browser
    ↓
S3 Static Website (prototype_v2.html)
    ↓
AWS Lambda Function URL (anthropic-proxy)
    ↓
Anthropic API
```

## Step 1: Deploy the Lambda Function

First, deploy the Lambda proxy function that will handle API calls to Anthropic.

### 1.1 Create the Lambda Function

```bash
cd lambda
zip function.zip anthropic-proxy.js
```

### 1.2 Create Lambda via AWS CLI

```bash
aws lambda create-function \
  --function-name anthropic-api-proxy \
  --runtime nodejs18.x \
  --role arn:aws:iam::YOUR_ACCOUNT_ID:role/lambda-execution-role \
  --handler anthropic-proxy.handler \
  --zip-file fileb://function.zip \
  --timeout 30 \
  --memory-size 256
```

Or use the AWS Console:
1. Go to AWS Lambda Console
2. Click "Create function"
3. Choose "Author from scratch"
4. Function name: `anthropic-api-proxy`
5. Runtime: Node.js 18.x
6. Upload `function.zip`

### 1.3 Set Environment Variable

```bash
aws lambda update-function-configuration \
  --function-name anthropic-api-proxy \
  --environment Variables={ANTHROPIC_API_KEY=sk-ant-YOUR_KEY_HERE}
```

Or via Console:
1. Go to Configuration → Environment variables
2. Add: `ANTHROPIC_API_KEY` = `sk-ant-YOUR_KEY_HERE`

### 1.4 Create Function URL

```bash
aws lambda create-function-url-config \
  --function-name anthropic-api-proxy \
  --auth-type NONE \
  --cors AllowOrigins="*",AllowMethods="POST,OPTIONS",AllowHeaders="Content-Type",MaxAge=86400
```

Or via Console:
1. Configuration → Function URL → Create function URL
2. Auth type: NONE
3. Configure CORS as shown above

**Important:** Copy the Function URL that's generated. You'll need it in the next step.

## Step 2: Update the HTML File

If your Lambda Function URL is different from the placeholder, update `prototype_v2.html`:

```javascript
// Find this line (around line 4443):
const response = await fetch('https://ocyxvmok64feluuk2n7loajw7q0rsjwr.lambda-url.us-east-1.on.aws/', {

// Replace with your actual Function URL:
const response = await fetch('https://YOUR_FUNCTION_URL_HERE/', {
```

## Step 3: Create S3 Bucket

### 3.1 Create the Bucket

```bash
aws s3 mb s3://contextual-data-discovery-demo --region us-east-1
```

Choose a unique bucket name (S3 bucket names must be globally unique).

### 3.2 Enable Static Website Hosting

```bash
aws s3 website s3://contextual-data-discovery-demo \
  --index-document prototype_v2.html \
  --error-document prototype_v2.html
```

Or via Console:
1. Go to S3 Console
2. Select your bucket
3. Properties → Static website hosting → Enable
4. Index document: `prototype_v2.html`
5. Error document: `prototype_v2.html`

### 3.3 Configure Bucket Policy for Public Access

Create a file named `bucket-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::contextual-data-discovery-demo/*"
    }
  ]
}
```

Apply the policy:

```bash
aws s3api put-bucket-policy \
  --bucket contextual-data-discovery-demo \
  --policy file://bucket-policy.json
```

### 3.4 Disable Block Public Access

```bash
aws s3api put-public-access-block \
  --bucket contextual-data-discovery-demo \
  --public-access-block-configuration \
    BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false
```

## Step 4: Upload the HTML File

```bash
aws s3 cp prototype_v2.html s3://contextual-data-discovery-demo/ \
  --content-type "text/html" \
  --cache-control "max-age=300"
```

## Step 5: Access Your Website

Your website will be available at:
```
http://contextual-data-discovery-demo.s3-website-us-east-1.amazonaws.com
```

The URL format is:
```
http://BUCKET_NAME.s3-website-REGION.amazonaws.com
```

## Step 6: (Optional) Configure CloudFront for HTTPS

For production use, set up CloudFront:

1. Create a CloudFront distribution
2. Origin: Your S3 bucket website endpoint
3. Enable HTTPS
4. Optionally add a custom domain with Route 53

## Updating the Application

To deploy updates:

```bash
aws s3 cp prototype_v2.html s3://contextual-data-discovery-demo/ \
  --content-type "text/html" \
  --cache-control "max-age=300"
```

## Cost Estimation

- **S3**: ~$0.023 per GB stored + $0.09 per GB transferred
- **Lambda**: First 1M requests free, then $0.20 per 1M requests
- **Anthropic API**: Charged per token (see Anthropic pricing)
- **CloudFront** (optional): ~$0.085 per GB transferred

For a demo/prototype with light usage: **~$5-20/month** depending on traffic.

## Troubleshooting

### CORS Errors

If you see CORS errors in the browser console:

1. Verify Lambda Function URL CORS configuration
2. Check that the Lambda response includes proper CORS headers
3. Ensure the Lambda handles OPTIONS preflight requests

### Lambda Errors

Check CloudWatch Logs:
```bash
aws logs tail /aws/lambda/anthropic-api-proxy --follow
```

### S3 Access Denied

1. Verify bucket policy is applied
2. Check that Block Public Access is disabled
3. Ensure the file has public read permissions

### API Key Issues

1. Verify the environment variable is set in Lambda
2. Check CloudWatch logs for authentication errors
3. Ensure your Anthropic API key is valid and has credits

## Security Considerations

**Current Setup (Demo):**
- Lambda Function URL is public (no authentication)
- S3 bucket is publicly readable
- Suitable for demos and prototypes

**Production Recommendations:**
1. Add API Gateway with API keys or Cognito authentication
2. Implement rate limiting
3. Use CloudFront with WAF for DDoS protection
4. Store Anthropic API key in AWS Secrets Manager
5. Enable CloudTrail for audit logging
6. Restrict S3 bucket access via CloudFront OAI

## Monitoring

Set up CloudWatch alarms for:
- Lambda errors and throttles
- S3 4xx/5xx errors
- Anthropic API costs (via Lambda custom metrics)

## Cleanup

To remove all resources:

```bash
# Delete S3 bucket contents
aws s3 rm s3://contextual-data-discovery-demo --recursive

# Delete S3 bucket
aws s3 rb s3://contextual-data-discovery-demo

# Delete Lambda function
aws lambda delete-function --function-name anthropic-api-proxy
```
