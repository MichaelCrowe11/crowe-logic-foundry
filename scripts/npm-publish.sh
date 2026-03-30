#!/bin/bash
# Quick npm publish with live OTP input
cd ~/Projects/crowe-logic-foundry
echo ""
echo "=== CROWE LOGIC — npm Publish ==="
echo ""
read -p "Enter your authenticator OTP code: " otp
npm publish --access public --otp="$otp"
if [ $? -eq 0 ]; then
    echo ""
    echo "Published! https://www.npmjs.com/package/crowe-logic"
else
    echo ""
    echo "Failed — try again with: bash scripts/npm-publish.sh"
fi
