package tv.frigorose.app;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/** Frigo Rosé TV : ouvre l'appli (mise à jour en ligne), avec une copie de secours intégrée. */
public class MainActivity extends Activity {
    private static final String ONLINE = "https://dylker24.github.io/frigo-rose-tv/";
    private static final String OFFLINE = "file:///android_asset/index.html";
    private WebView web;
    private boolean usedFallback = false;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        web = new WebView(this);
        web.setBackgroundColor(Color.BLACK);
        setContentView(web);
        hideBars();

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setAllowFileAccess(true);
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowUniversalAccessFromFileURLs(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setUserAgentString(s.getUserAgentString() + " FrigoRoseTV/1");

        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView v, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) fallback(v);
            }
            @SuppressWarnings("deprecation")
            @Override
            public void onReceivedError(WebView v, int code, String desc, String url) {
                if (url != null && url.startsWith(ONLINE)) fallback(v);
            }
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) { return false; }
        });
        web.setFocusable(true);
        web.setFocusableInTouchMode(true);
        web.requestFocus();
        web.loadUrl(ONLINE + "?v=" + System.currentTimeMillis());
    }

    private void fallback(WebView v) {
        if (usedFallback) return;
        usedFallback = true;
        v.loadUrl(OFFLINE);
    }

    private void hideBars() {
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideBars();
    }

    /** Touches de la télécommande que la page ne reçoit pas toute seule. */
    private String keyFor(int code) {
        switch (code) {
            case KeyEvent.KEYCODE_BACK: return "Escape";
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE: case KeyEvent.KEYCODE_MEDIA_PLAY: case KeyEvent.KEYCODE_MEDIA_PAUSE: return "MediaPlayPause";
            case KeyEvent.KEYCODE_MEDIA_REWIND: return "MediaRewind";
            case KeyEvent.KEYCODE_MEDIA_FAST_FORWARD: return "MediaFastForward";
            case KeyEvent.KEYCODE_CHANNEL_UP: return "ChannelUp";
            case KeyEvent.KEYCODE_CHANNEL_DOWN: return "ChannelDown";
            case KeyEvent.KEYCODE_GUIDE: return "Guide";
            case KeyEvent.KEYCODE_INFO: return "Info";
            case KeyEvent.KEYCODE_MENU: return "Info";
            default: return null;
        }
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        String k = keyFor(e.getKeyCode());
        if (k != null) {
            if (e.getAction() == KeyEvent.ACTION_DOWN && e.getRepeatCount() == 0) {
                web.evaluateJavascript("document.dispatchEvent(new KeyboardEvent('keydown',{key:'" + k + "',bubbles:true,cancelable:true}))", null);
            }
            return true;
        }
        return super.dispatchKeyEvent(e);
    }

    @Override
    protected void onPause() { super.onPause(); if (web != null) { web.evaluateJavascript("try{document.querySelector('video').pause()}catch(e){}", null); web.onPause(); } }

    @Override
    protected void onResume() { super.onResume(); hideBars(); if (web != null) { web.onResume(); web.evaluateJavascript("try{window.frtvResume&&frtvResume()}catch(e){}", null); } }
}
