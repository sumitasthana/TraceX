# Contextual Data Discovery - AWS Deployment

A working prototype of a contextual data discovery system for regulated banking environments. The conversational layer runs on Claude Sonnet 4, while classification, requirements, and fitness evaluation use deterministic logic for governance defensibility.

## Architecture

```
┌─────────────┐
│   Browser   │
└──────┬──────┘
       │ HTTPS
       ▼
┌─────────────────────┐
│  S3 Static Website  │
│  prototype_v2.html  │
└──────┬──────────────┘
       │ HTTPS
       ▼
┌──────────────────────┐
│  Lambda Function URL │
│  anthropic-proxy     │
└──────┬───────────────┘
       │ HTTPS
       ▼
┌──────────────────┐
│  Anthropic API   │
│  Claude Sonnet 4 │
└──────────────────┘
```

## Features

- **Contextual Discovery**: Finds data assets based on use case context
- **Governance-First**: Deterministic classification and fitness evaluation
- **BIAN-Aligned**: Maps business concepts to physical data assets
- **Evidence-Based**: Provides detailed governance evidence for each recommendation
- **Agent-Ready**: Outputs structured JSON contracts for downstream consumption

## Quick Start

### Prerequisites

- AWS Account with appropriate permissions
- AWS CLI installed and configured
- Anthropic API key (get one at https://console.anthropic.com/)
- Node.js 18+ (for Lambda development)

### 1. Deploy Lambda Function

```bash
cd lambda
zip function.zip anthropic-proxy.js

# Create Lambda function
aws lambda create-function \
  --function-name anthropic-api-proxy \
  --runtime nodejs18.x \
  --role arn:aws:iam::YOUR_ACCOUNT_ID:role/lambda-execution-role \
  --handler anthropic-proxy.handler \
  --zip-file fileb://function.zip \
  --timeout 30 \
  --memory-size 256

# Set Anthropic API key
aws lambda update-function-configuration \
  --function-name anthropic-api-proxy \
  --environment Variables={ANTHROPIC_API_KEY=sk-ant-YOUR_KEY_HERE}

# Create Function URL with CORS
aws lambda create-function-url-config \
  --function-name anthropic-api-proxy \
  --auth-type NONE \
  --cors AllowOrigins="*",AllowMethods="POST,OPTIONS",AllowHeaders="Content-Type",MaxAge=86400
```

**Copy the Function URL** - you'll need it in the next step.

### 2. Update HTML Configuration

If your Lambda Function URL differs from the default, update `prototype_v2.html` line 4443:

```javascript
const response = await fetch('https://YOUR_FUNCTION_URL_HERE/', {
```

### 3. Deploy to S3

**Using PowerShell (Windows):**
```powershell
.\deploy.ps1 -BucketName "your-unique-bucket-name" -Region "us-east-1"
```

**Using Bash (Linux/Mac):**
```bash
chmod +x deploy.sh
./deploy.sh your-unique-bucket-name us-east-1
```

**Manual deployment:**
```bash
# Create bucket
aws s3 mb s3://your-bucket-name --region us-east-1

# Enable static website hosting
aws s3 website s3://your-bucket-name \
  --index-document prototype_v2.html

# Upload file
aws s3 cp prototype_v2.html s3://your-bucket-name/ \
  --content-type "text/html"
```

### 4. Access Your Application

Your application will be available at:
```
http://your-bucket-name.s3-website-us-east-1.amazonaws.com
```

## Project Structure

```
prototype/
├── prototype_v2.html          # Main application (single-file HTML/CSS/JS)
├── lambda/
│   ├── anthropic-proxy.js     # Lambda function for API proxy
│   ├── package.json           # Lambda package metadata
│   └── README.md              # Lambda deployment guide
├── S3_DEPLOYMENT.md           # Detailed S3 deployment guide
├── bucket-policy.json         # S3 bucket policy template
├── deploy.sh                  # Bash deployment script
├── deploy.ps1                 # PowerShell deployment script
└── README.md                  # This file
```

## Configuration

### Lambda Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key (sk-ant-...) | Yes |

### Lambda Function URL Settings

- **Auth Type**: NONE (public access)
- **CORS**: Enabled for all origins
- **Timeout**: 30 seconds
- **Memory**: 256 MB

### S3 Bucket Settings

- **Static Website Hosting**: Enabled
- **Index Document**: prototype_v2.html
- **Public Access**: Enabled (for demo purposes)
- **CORS**: Not required (handled by Lambda)

## Usage

1. Open the S3 website URL in your browser
2. The application starts automatically (no API key required from users)
3. Describe your data use case in natural language
4. The system will:
   - Ask clarifying questions if needed
   - Classify your use case against governance profiles
   - Map your business concepts to physical data assets
   - Evaluate fitness of each candidate
   - Provide detailed evidence and recommendations

### Example Use Cases

**Regulatory Reporting:**
> "I need deposit balance data for our CCAR liquidity submission this cycle. Specifically, monthly aggregates by deposit type for the past eight quarters."

**Model Development:**
> "I'm building a model that predicts which deposit customers are likely to attrite in the next 90 days, so we can trigger retention offers."

**Exploratory Analysis:**
> "I'm exploring whether deposit growth patterns in the Pittsburgh metro look different from Cleveland — just trying to see if there's a story worth investigating."

## Updating the Application

To deploy updates to the HTML file:

```bash
aws s3 cp prototype_v2.html s3://your-bucket-name/ \
  --content-type "text/html" \
  --cache-control "max-age=300"
```

To update the Lambda function:

```bash
cd lambda
zip function.zip anthropic-proxy.js
aws lambda update-function-code \
  --function-name anthropic-api-proxy \
  --zip-file fileb://function.zip
```

## Cost Estimation

For a demo/prototype with light usage:

- **S3 Storage**: ~$0.023 per GB/month (negligible for single HTML file)
- **S3 Data Transfer**: ~$0.09 per GB transferred out
- **Lambda Invocations**: First 1M requests free, then $0.20 per 1M
- **Lambda Duration**: First 400,000 GB-seconds free
- **Anthropic API**: ~$3 per million input tokens, ~$15 per million output tokens

**Estimated monthly cost**: $5-20 depending on usage (mostly Anthropic API costs)

## Security Considerations

### Current Setup (Demo/Prototype)

✅ API key stored securely in Lambda environment variables  
✅ No API key exposed to client browser  
✅ CORS configured to prevent unauthorized origins (in production)  
⚠️ Lambda Function URL is public (no authentication)  
⚠️ S3 bucket is publicly readable  

### Production Recommendations

1. **Add Authentication**: Use API Gateway with Cognito or API keys
2. **Rate Limiting**: Implement throttling to prevent abuse
3. **WAF Protection**: Use CloudFront with AWS WAF
4. **Secrets Management**: Store API key in AWS Secrets Manager
5. **Monitoring**: Set up CloudWatch alarms and dashboards
6. **Audit Logging**: Enable CloudTrail for compliance
7. **Private Access**: Use CloudFront OAI to restrict S3 access

## Troubleshooting

### CORS Errors

Check Lambda Function URL CORS configuration:
```bash
aws lambda get-function-url-config --function-name anthropic-api-proxy
```

### Lambda Errors

View CloudWatch logs:
```bash
aws logs tail /aws/lambda/anthropic-api-proxy --follow
```

### S3 Access Denied

Verify bucket policy and public access settings:
```bash
aws s3api get-bucket-policy --bucket your-bucket-name
aws s3api get-public-access-block --bucket your-bucket-name
```

### API Key Issues

Verify environment variable is set:
```bash
aws lambda get-function-configuration --function-name anthropic-api-proxy
```

## Monitoring

### CloudWatch Metrics to Monitor

- Lambda invocations and errors
- Lambda duration and throttles
- S3 4xx and 5xx errors
- Anthropic API costs (custom metric)

### Setting Up Alarms

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name lambda-errors \
  --alarm-description "Alert on Lambda errors" \
  --metric-name Errors \
  --namespace AWS/Lambda \
  --statistic Sum \
  --period 300 \
  --threshold 5 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=FunctionName,Value=anthropic-api-proxy
```

## Cleanup

To remove all resources:

```bash
# Delete S3 bucket contents and bucket
aws s3 rm s3://your-bucket-name --recursive
aws s3 rb s3://your-bucket-name

# Delete Lambda function
aws lambda delete-function-url-config --function-name anthropic-api-proxy
aws lambda delete-function --function-name anthropic-api-proxy
```

## Development

### Local Testing

The HTML file can be opened directly in a browser for UI testing (without Lambda integration):

```bash
# Simple HTTP server
python -m http.server 8000
# Then open http://localhost:8000/prototype_v2.html
```

### Lambda Local Testing

Test the Lambda function locally:

```bash
cd lambda
node -e "
const handler = require('./anthropic-proxy').handler;
const event = {
  requestContext: { http: { method: 'POST' } },
  body: JSON.stringify({
    model: 'claude-sonnet-4-20250514',
    max_tokens: 100,
    system: 'You are helpful.',
    messages: [{role: 'user', content: 'Hello'}]
  })
};
handler(event).then(console.log);
"
```

## Support

For issues or questions:
- Check the troubleshooting section above
- Review CloudWatch logs for detailed error messages
- Consult AWS documentation for Lambda and S3
- Review Anthropic API documentation

## License

This is a prototype/demo application. Adjust licensing as needed for your organization.
