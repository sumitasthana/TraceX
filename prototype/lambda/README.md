# Anthropic API Proxy Lambda Function

This Lambda function acts as a secure proxy between your S3-hosted static site and the Anthropic API.

## Deployment Steps

### 1. Create the Lambda Function

1. Go to AWS Lambda Console
2. Click "Create function"
3. Choose "Author from scratch"
4. Function name: `anthropic-api-proxy`
5. Runtime: Node.js 18.x or later
6. Architecture: x86_64
7. Click "Create function"

### 2. Upload the Code

```bash
cd lambda
zip function.zip anthropic-proxy.js
```

Then upload `function.zip` to your Lambda function via the AWS Console or CLI:

```bash
aws lambda update-function-code \
  --function-name anthropic-api-proxy \
  --zip-file fileb://function.zip
```

### 3. Configure Environment Variables

In the Lambda Console:
1. Go to Configuration → Environment variables
2. Add a new variable:
   - Key: `ANTHROPIC_API_KEY`
   - Value: Your Anthropic API key (starts with `sk-ant-`)

### 4. Create Function URL

1. In the Lambda Console, go to Configuration → Function URL
2. Click "Create function URL"
3. Auth type: NONE (since we're using CORS)
4. Configure CORS:
   - Allow origin: `*` (or specify your S3 bucket URL)
   - Allow methods: POST, OPTIONS
   - Allow headers: Content-Type
   - Max age: 86400
5. Save

You'll get a URL like: `https://ocyxvmok64feluuk2n7loajw7q0rsjwr.lambda-url.us-east-1.on.aws/`

### 5. Update the HTML File

Replace the Lambda URL in `prototype_v2.html` with your actual Function URL.

### 6. Test the Lambda

Test payload:
```json
{
  "body": "{\"model\":\"claude-sonnet-4-20250514\",\"max_tokens\":100,\"system\":\"You are a helpful assistant.\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}]}"
}
```

## Security Considerations

- The Anthropic API key is stored as an environment variable in Lambda (encrypted at rest)
- CORS is configured to allow requests from your S3 bucket
- Consider adding API Gateway with API keys for production use
- Monitor Lambda logs in CloudWatch for any issues

## Cost Estimation

- Lambda: First 1M requests/month are free, then $0.20 per 1M requests
- Anthropic API: Charged per token usage
- No additional costs for Function URL

## Troubleshooting

If you get CORS errors:
1. Check that OPTIONS method is allowed in Function URL configuration
2. Verify CORS headers in the Lambda response
3. Check browser console for specific CORS error messages

If API calls fail:
1. Check CloudWatch Logs for Lambda errors
2. Verify ANTHROPIC_API_KEY is set correctly
3. Test the Lambda function directly in the AWS Console
