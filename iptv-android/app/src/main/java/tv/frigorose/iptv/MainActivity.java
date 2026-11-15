package tv.frigorose.iptv;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/** Frigo Rosé IPTV : l'interface est une page (mise à jour toute seule depuis internet, copie gardée sur l'appareil),
 *  ouverte comme un fichier local pour pouvoir parler à n'importe quel serveur IPTV. La vidéo passe par un vrai lecteur Android. */
public class MainActivity extends Activity {
    private static final String ONLINE = "https://dylker24.github.io/frigo-rose-tv/iptv/";
    private static final String OFFLINE = "file:///android_asset/index.html";
    private static final int PLAY = 7;
    private WebView web;
    private boolean usedFallback = false;
    private boolean quitting = false;
    private boolean inPlayer = false;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#07070A"));
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
        s.setUserAgentString(s.getUserAgentString() + " FrigoRoseIPTV/2" + (isTv() ? "" : " FrigoRoseTablet"));

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public Bitmap getDefaultVideoPoster() { return Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888); }
        });
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView v, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) fallback(v);
            }
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) { return false; }
        });
        web.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public void exit() { runOnUiThread(() -> quit()); }

            @JavascriptInterface
            public void play(String json) {
                runOnUiThread(() -> {
                    try {
                        Intent i = new Intent(MainActivity.this, PlayerActivity.class);
                        i.putExtra("data", json);
                        inPlayer = true;
                        startActivityForResult(i, PLAY);
                    } catch (Exception e) { inPlayer = false; }
                });
            }
        }, "FRIPTV");
        web.setFocusable(true);
        web.setFocusableInTouchMode(true);
        web.requestFocus();

        final File local = new File(getFilesDir(), "index.html");
        new Thread(() -> {
            byte[] fresh = download(ONLINE + "?v=" + System.currentTimeMillis());
            try {
                if (fresh != null && fresh.length > 20000) {
                    FileOutputStream o = new FileOutputStream(local); o.write(fresh); o.close();
                } else if (!local.exists()) {
                    InputStream in = getAssets().open("index.html"); byte[] b = readAll(in);
                    FileOutputStream o = new FileOutputStream(local); o.write(b); o.close();
                }
            } catch (Exception e) { }
            runOnUiThread(() -> { if (!quitting) web.loadUrl(local.exists() ? "file://" + local.getAbsolutePath() : OFFLINE); });
        }).start();
    }

    private static byte[] readAll(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream(); byte[] buf = new byte[16384]; int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        in.close(); return out.toByteArray();
    }

    private static byte[] download(String u) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(u).openConnection();
            c.setConnectTimeout(5000); c.setReadTimeout(8000); c.setUseCaches(false);
            if (c.getResponseCode() != 200) return null;
            return readAll(c.getInputStream());
        } catch (Exception e) { return null; }
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
            case KeyEvent.KEYCODE_CHANNEL_UP: return "ChannelUp";
            case KeyEvent.KEYCODE_CHANNEL_DOWN: return "ChannelDown";
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

    private boolean isTv() {
        android.app.UiModeManager m = (android.app.UiModeManager) getSystemService(UI_MODE_SERVICE);
        return (m != null && m.getCurrentModeType() == android.content.res.Configuration.UI_MODE_TYPE_TELEVISION)
            || getPackageManager().hasSystemFeature("android.software.leanback");
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (req == PLAY) {
            inPlayer = false;
            String r = data != null && data.getStringExtra("result") != null ? data.getStringExtra("result") : "{}";
            if (web != null && !quitting) web.evaluateJavascript("try{window.iptvClosed&&iptvClosed(" + JSONObject.quote(r) + ")}catch(e){}", null);
        }
    }

    private void quit() {
        if (quitting) return;
        quitting = true;
        try { web.loadUrl("about:blank"); web.destroy(); } catch (Exception e) { }
        finishAndRemoveTask();
        new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(() -> android.os.Process.killProcess(android.os.Process.myPid()), 400);
    }

    /** Bouton Accueil de la télécommande : on quitte pour de vrai (sauf quand on part regarder une vidéo). */
    @Override
    protected void onUserLeaveHint() { super.onUserLeaveHint(); if (!inPlayer) quit(); }

    @Override
    protected void onStop() { super.onStop(); if (!isChangingConfigurations() && !inPlayer) quit(); }

    @Override
    protected void onPause() { super.onPause(); if (web != null && !quitting) web.onPause(); }

    @Override
    protected void onResume() { super.onResume(); hideBars(); if (web != null && !quitting) web.onResume(); }
}
