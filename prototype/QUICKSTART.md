# Quick Start Guide - 5 Minutes to Deployment

## Prerequisites
- AWS CLI configured
- Anthropic API key

## Step 1: Deploy Lambda (2 minutes)

```bash
cd lambda
zip function.zip anthropic-proxy.js

aws lambda create-function \
  --function-name anthropic-api-proxy \
  --runtime nodejs18.x \
  --role arn:aws:iam::YOUR_ACCOUNT_ID:role/lambda-execution-role \
  --handler anthropic-proxy.handler \
  --zip-file fileb://function.zip

aws lambda update-function-configuration \
  --function-name anthropic-api-proxy \
  --environment Variables={ANTHROPIC_API_KEY=YOUR_KEY}

aws lambda create-function-url-config \
  --function-name anthropic-api-proxy \
  --auth-type NONE \
  --cors AllowOrigins="*",AllowMethods="POST,OPTIONS",AllowHeaders="Content-Type"
```

**Copy the Function URL from the output!**

## Step 2: Update HTML (30 seconds)

Edit `prototype_v2.html` line 4443:
```javascript
const response = await fetch('YOUR_FUNCTION_URL_HERE/', {
```

## Step 3: Deploy to S3 (2 minutes)

**Windows:**
```powershell
.\deploy.ps1
```

**Mac/Linux:**
```bash
chmod +x deploy.sh
./deploy.sh
```

## Step 4: Access Your App

Open the URL shown in the deployment output:
```
http://contextual-data-discovery-demo.s3-website-us-east-1.amazonaws.com
```

## Done! 🎉

Your application is now live and ready to use.

## Troubleshooting

**CORS Error?**
```bash
aws lambda get-function-url-config --function-name anthropic-api-proxy
```

**Lambda Error?**
```bash
aws logs tail /aws/lambda/anthropic-api-proxy --follow
```

**Need to update?**
```bash
aws s3 cp prototype_v2.html s3://contextual-data-discovery-demo/
```
