# Deployment Checklist

Use this checklist to ensure a successful deployment.

## Pre-Deployment

- [ ] AWS CLI installed and configured
- [ ] AWS account has necessary permissions (Lambda, S3, IAM)
- [ ] Anthropic API key obtained (https://console.anthropic.com/)
- [ ] Anthropic API key has sufficient credits
- [ ] Decided on AWS region (e.g., us-east-1)
- [ ] Chosen unique S3 bucket name

## Lambda Deployment

- [ ] Navigate to `lambda/` directory
- [ ] Create `function.zip` with `anthropic-proxy.js`
- [ ] Create Lambda function (or use AWS Console)
- [ ] Set runtime to Node.js 18.x or later
- [ ] Configure timeout to 30 seconds
- [ ] Set memory to 256 MB
- [ ] Add environment variable: `ANTHROPIC_API_KEY`
- [ ] Create Lambda Function URL
- [ ] Set auth type to NONE
- [ ] Configure CORS:
  - [ ] Allow origins: `*` (or specific domain)
  - [ ] Allow methods: POST, OPTIONS
  - [ ] Allow headers: Content-Type
  - [ ] Max age: 86400
- [ ] Copy Function URL for next step
- [ ] Test Lambda function with sample payload

## HTML Configuration

- [ ] Open `prototype_v2.html`
- [ ] Find line 4443 (the `fetch` call)
- [ ] Replace Lambda URL with your actual Function URL
- [ ] Save the file

## S3 Deployment

### Option A: Automated Script

**Windows (PowerShell):**
- [ ] Open PowerShell in prototype directory
- [ ] Run: `.\deploy.ps1 -BucketName "your-bucket-name"`
- [ ] Verify success message
- [ ] Copy website URL from output

**Mac/Linux (Bash):**
- [ ] Open terminal in prototype directory
- [ ] Run: `chmod +x deploy.sh`
- [ ] Run: `./deploy.sh your-bucket-name us-east-1`
- [ ] Verify success message
- [ ] Copy website URL from output

### Option B: Manual Deployment

- [ ] Create S3 bucket with unique name
- [ ] Enable static website hosting
- [ ] Set index document to `prototype_v2.html`
- [ ] Disable "Block all public access"
- [ ] Apply bucket policy from `bucket-policy.json`
- [ ] Upload `prototype_v2.html` to bucket
- [ ] Set content-type to `text/html`
- [ ] Note the website endpoint URL

## Testing

- [ ] Open S3 website URL in browser
- [ ] Verify page loads without errors
- [ ] Check browser console for errors
- [ ] Test with a sample query:
  - [ ] "I need deposit balance data for CCAR reporting"
- [ ] Verify agent responds appropriately
- [ ] Test phase progression (Elicit → Classify → Catalog → Resolve)
- [ ] Check that verdicts are displayed
- [ ] Verify sidebar shows classification and requirements

## Monitoring Setup

- [ ] Open CloudWatch console
- [ ] Navigate to Lambda → anthropic-api-proxy → Monitoring
- [ ] Review available metrics
- [ ] (Optional) Create alarm for Lambda errors
- [ ] (Optional) Create alarm for Lambda throttles
- [ ] (Optional) Create dashboard for key metrics

## Documentation

- [ ] Share S3 website URL with stakeholders
- [ ] Document Lambda Function URL (keep secure)
- [ ] Note AWS region used
- [ ] Save Anthropic API key securely (password manager)
- [ ] Document any customizations made

## Security Review

- [ ] Verify API key is NOT in HTML file
- [ ] Confirm API key is in Lambda environment variables
- [ ] Check Lambda Function URL CORS settings
- [ ] Review S3 bucket policy
- [ ] Consider adding rate limiting (production)
- [ ] Consider adding authentication (production)

## Cost Monitoring

- [ ] Set up AWS Billing alerts
- [ ] Monitor Anthropic API usage
- [ ] Review Lambda invocation counts
- [ ] Check S3 data transfer costs
- [ ] Set budget alerts if needed

## Post-Deployment

- [ ] Test from different browsers (Chrome, Firefox, Safari)
- [ ] Test from different devices (desktop, mobile)
- [ ] Test from different networks (office, home, mobile)
- [ ] Verify CORS works correctly
- [ ] Monitor CloudWatch logs for errors
- [ ] Document any issues encountered

## Troubleshooting (If Issues Occur)

### CORS Errors
- [ ] Check Lambda Function URL CORS configuration
- [ ] Verify OPTIONS method is allowed
- [ ] Check browser console for specific error
- [ ] Test Lambda directly in AWS Console

### Lambda Errors
- [ ] Check CloudWatch Logs: `/aws/lambda/anthropic-api-proxy`
- [ ] Verify ANTHROPIC_API_KEY is set
- [ ] Test with sample payload in Lambda console
- [ ] Check Lambda timeout and memory settings

### S3 Access Denied
- [ ] Verify bucket policy is applied
- [ ] Check "Block public access" is disabled
- [ ] Confirm file has public read permissions
- [ ] Test bucket policy with AWS Policy Simulator

### API Key Issues
- [ ] Verify API key starts with `sk-ant-`
- [ ] Check API key has sufficient credits
- [ ] Test API key directly with Anthropic API
- [ ] Ensure no extra spaces in environment variable

## Optional Enhancements

- [ ] Set up CloudFront distribution for HTTPS
- [ ] Configure custom domain with Route 53
- [ ] Add API Gateway for authentication
- [ ] Implement rate limiting
- [ ] Add request logging to S3
- [ ] Set up automated backups
- [ ] Create staging environment
- [ ] Add CI/CD pipeline

## Rollback Plan

If deployment fails:
- [ ] Keep original `prototype_v2.html` backup
- [ ] Document Lambda Function URL
- [ ] Save CloudWatch logs
- [ ] Delete Lambda function if needed
- [ ] Delete S3 bucket if needed
- [ ] Restore from backup if needed

## Success Criteria

✅ S3 website loads without errors  
✅ User can submit queries  
✅ Agent responds with classifications  
✅ Verdicts are displayed correctly  
✅ No CORS errors in console  
✅ Lambda logs show successful invocations  
✅ Anthropic API calls are working  
✅ All phases complete successfully  

## Completion

- [ ] All checklist items completed
- [ ] Website is live and functional
- [ ] Monitoring is in place
- [ ] Documentation is updated
- [ ] Stakeholders are notified
- [ ] Support plan is documented

---

**Deployment Date**: _______________  
**Deployed By**: _______________  
**S3 Website URL**: _______________  
**Lambda Function URL**: _______________ (keep secure)  
**AWS Region**: _______________  
**Notes**: _______________
