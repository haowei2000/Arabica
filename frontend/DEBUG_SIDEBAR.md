# Debug: Conversation History Sidebar Not Showing

## Check these in your browser console:

### 1. Open Browser DevTools (F12)

### 2. Check if sidebar element exists:
```javascript
document.querySelector('aside')
```
Should return the sidebar element.

### 3. Check React Query state:
```javascript
// In console, check if conversations are being loaded
// Look for network requests to /api/conversations/
```

### 4. Check for JavaScript errors:
- Look in the Console tab for any red error messages

### 5. Check CSS/Visibility:
```javascript
// Check if sidebar is hidden
const sidebar = document.querySelector('aside');
console.log('Sidebar display:', window.getComputedStyle(sidebar).display);
console.log('Sidebar transform:', window.getComputedStyle(sidebar).transform);
console.log('Sidebar width:', window.getComputedStyle(sidebar).width);
```

### 6. Check if data is loaded:
Open Network tab and filter by "conversations" - you should see API requests when you load the chat page.

## Common Issues:

1. **Sidebar is hidden on mobile**: Try clicking the menu icon (☰) in the header
2. **No conversations**: The API might be returning empty data
3. **Auth issues**: Check if you're logged in properly
4. **CORS errors**: Check console for CORS-related errors

## Manual Test:

Navigate to: http://localhost:3000 (or your Vite dev server URL, e.g., http://<YOUR_IP>:3000 for LAN access)
1. Login
2. Select an app
3. You should see the sidebar on the left with "对话历史" header
4. If not visible, press Ctrl+Shift+C and try to find the aside element

## Quick Fix:

If sidebar is there but conversations don't load, check your app_id by running this in console:
```javascript
// This should show your current app ID
localStorage.getItem('currentAppId')
```
