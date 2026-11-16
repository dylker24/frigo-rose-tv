package tv.frigorose.iptv;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.annotation.OptIn;
import androidx.media3.common.C;
import androidx.media3.common.MediaItem;
import androidx.media3.common.MediaMetadata;
import androidx.media3.common.MimeTypes;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.Player;
import androidx.media3.common.util.UnstableApi;
import androidx.media3.datasource.DefaultHttpDataSource;
import androidx.media3.exoplayer.DefaultLoadControl;
import androidx.media3.exoplayer.DefaultRenderersFactory;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory;
import androidx.media3.exoplayer.trackselection.DefaultTrackSelector;
import androidx.media3.extractor.DefaultExtractorsFactory;
import androidx.media3.extractor.ts.DefaultTsPayloadReaderFactory;
import androidx.media3.ui.PlayerView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** Le lecteur : chaînes en direct (zapping ⬆ ⬇), films et séries (épisode suivant tout seul). */
@OptIn(markerClass = UnstableApi.class)
public class PlayerActivity extends Activity {
    private ExoPlayer player;
    private PlayerView view;
    private LinearLayout info;
    private TextView infoTitle, infoSub, errText;
    private ProgressBar spin;
    private boolean live;
    private final List<String[]> items = new ArrayList<>();  // url, titre, sous-titre
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final Runnable hideInfo = () -> { if (info != null) info.setVisibility(View.GONE); };
    private int retries = 0;
    // surveillance : si l'image ne bouge plus alors qu'on est en lecture, on reconnecte tout seul
    private long lastPos = -1, stuckSince = 0, goodSince = 0;
    private final Runnable watch = new Runnable() {
        @Override public void run() {
            if (player == null) return;
            long now = System.currentTimeMillis();
            boolean playing = player.getPlayWhenReady() && player.getPlaybackState() != Player.STATE_ENDED && player.getPlaybackState() != Player.STATE_IDLE;
            long pos = player.getCurrentPosition();
            if (!playing || pos != lastPos) { stuckSince = now; lastPos = pos; }
            if (player.isPlaying()) { if (goodSince == 0) goodSince = now; if (now - goodSince > 30000) retries = 0; } else goodSince = 0;
            if (playing && now - stuckSince > (live ? 9000 : 12000)) { stuckSince = now; reconnect(); }
            ui.postDelayed(this, 1500);
        }
    };
    private void reconnect() {
        if (player == null) return;
        errText.setVisibility(View.GONE);
        int i = player.getCurrentMediaItemIndex();
        if (live) player.seekToDefaultPosition(i);
        else player.seekTo(i, Math.max(0, player.getCurrentPosition() - 2000));
        player.prepare(); player.play();
    }
    private boolean done = false;

    private int dp(float v) { return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics()); }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);

        int index = 0; long start = 0;
        try {
            JSONObject d = new JSONObject(getIntent().getStringExtra("data"));
            live = d.optBoolean("live");
            index = d.optInt("index");
            start = d.optLong("start");
            JSONArray l = d.getJSONArray("list");
            for (int i = 0; i < l.length(); i++) {
                JSONObject o = l.getJSONObject(i);
                items.add(new String[]{o.optString("url"), o.optString("title"), o.optString("sub")});
            }
        } catch (Exception e) { finishWith(0, 0, 0); return; }
        if (items.isEmpty()) { finishWith(0, 0, 0); return; }

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        view = (PlayerView) getLayoutInflater().inflate(R.layout.frs_player, root, false);
        view.setKeepContentOnPlayerReset(false);
        view.setShowBuffering(PlayerView.SHOW_BUFFERING_NEVER);
        view.setUseController(true);
        view.setControllerShowTimeoutMs(live ? 5000 : 3500);
        view.setShowNextButton(items.size() > 1);
        view.setShowPreviousButton(items.size() > 1);
        view.setShowFastForwardButton(!live);
        view.setShowRewindButton(!live);
        view.setControllerAutoShow(false);
        view.setControllerVisibilityListener((PlayerView.ControllerVisibilityListener) v -> { if (v == View.VISIBLE) showInfo(); });
        root.addView(view, new FrameLayout.LayoutParams(-1, -1));

        spin = new ProgressBar(this);
        FrameLayout.LayoutParams sp = new FrameLayout.LayoutParams(dp(56), dp(56), Gravity.CENTER);
        root.addView(spin, sp);

        // bandeau de la chaîne / du titre
        info = new LinearLayout(this);
        info.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.parseColor("#EB111116")); bg.setCornerRadius(dp(14)); bg.setStroke(dp(1), Color.parseColor("#2A2A34"));
        info.setBackground(bg);
        info.setPadding(dp(20), dp(14), dp(24), dp(14));
        infoTitle = new TextView(this); infoTitle.setTextColor(Color.WHITE); infoTitle.setTextSize(20); infoTitle.setTypeface(Typeface.DEFAULT_BOLD);
        infoSub = new TextView(this); infoSub.setTextColor(Color.parseColor("#A3A3B2")); infoSub.setTextSize(14);
        info.addView(infoTitle); info.addView(infoSub);
        FrameLayout.LayoutParams ip = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.START);
        ip.setMargins(dp(40), 0, dp(40), dp(36));
        root.addView(info, ip);

        errText = new TextView(this);
        errText.setTextColor(Color.WHITE); errText.setTextSize(18); errText.setGravity(Gravity.CENTER);
        errText.setBackgroundColor(Color.parseColor("#B3000000")); errText.setPadding(dp(30), dp(30), dp(30), dp(30));
        errText.setVisibility(View.GONE);
        root.addView(errText, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);

        DefaultTrackSelector ts = new DefaultTrackSelector(this);
        ts.setParameters(ts.buildUponParameters().setPreferredAudioLanguages("fr", "fra", "fre").setPreferredTextLanguage("fr"));
        DefaultHttpDataSource.Factory http = new DefaultHttpDataSource.Factory()
                .setUserAgent("VLC/3.0.20 LibVLC/3.0.20")
                .setAllowCrossProtocolRedirects(true)
                .setConnectTimeoutMs(15000).setReadTimeoutMs(20000);
        DefaultExtractorsFactory ex = new DefaultExtractorsFactory()
                .setTsExtractorFlags(DefaultTsPayloadReaderFactory.FLAG_ALLOW_NON_IDR_KEYFRAMES | DefaultTsPayloadReaderFactory.FLAG_DETECT_ACCESS_UNITS);
        DefaultRenderersFactory rf = new DefaultRenderersFactory(this).setEnableDecoderFallback(true)
                .setExtensionRendererMode(DefaultRenderersFactory.EXTENSION_RENDERER_MODE_PREFER);
        DefaultLoadControl lc = new DefaultLoadControl.Builder()
                .setBufferDurationsMs(live ? 15000 : 30000, live ? 45000 : 90000, 2000, 4000)
                .setPrioritizeTimeOverSizeThresholds(true).build();
        player = new ExoPlayer.Builder(this, rf)
                .setLoadControl(lc)
                .setSeekBackIncrementMs(10000)
                .setSeekForwardIncrementMs(10000)
                .setTrackSelector(ts)
                .setMediaSourceFactory(new DefaultMediaSourceFactory(http, ex))
                .build();
        view.setPlayer(player);

        List<MediaItem> mi = new ArrayList<>();
        for (String[] it : items) {
            MediaItem.Builder b = new MediaItem.Builder().setUri(Uri.parse(it[0]))
                    .setMediaMetadata(new MediaMetadata.Builder().setTitle(it[1]).setSubtitle(it[2]).build());
            String low = it[0].toLowerCase();
            int q = low.indexOf('?'); if (q > 0) low = low.substring(0, q);
            if (low.endsWith(".m3u8")) b.setMimeType(MimeTypes.APPLICATION_M3U8);
            if (live) b.setLiveConfiguration(new MediaItem.LiveConfiguration.Builder().setTargetOffsetMs(8000).build());
            mi.add(b.build());
        }
        // en direct, si le serveur coupe le flux, on relance la même chaîne (au lieu de rester figé)
        player.setRepeatMode(live ? Player.REPEAT_MODE_ONE : Player.REPEAT_MODE_OFF);
        player.setMediaItems(mi, Math.max(0, Math.min(index, mi.size() - 1)), live ? C.TIME_UNSET : start);
        player.addListener(new Player.Listener() {
            @Override
            public void onPlaybackStateChanged(int st) {
                spin.setVisibility(st == Player.STATE_BUFFERING ? View.VISIBLE : View.GONE);
                if (st == Player.STATE_READY) { retries = 0; errText.setVisibility(View.GONE); }
                if (st == Player.STATE_ENDED && !live) finishWith(player.getCurrentMediaItemIndex(), 0, 0);
            }
            @Override
            public void onMediaItemTransition(MediaItem m, int reason) {
                retries = 0; errText.setVisibility(View.GONE);
                showInfo();
            }
            @Override
            public void onPlayerError(PlaybackException e) {
                spin.setVisibility(View.GONE);
                if (e.errorCode == PlaybackException.ERROR_CODE_BEHIND_LIVE_WINDOW && player != null) {
                    // après une longue pause sur une chaîne : on revient au direct
                    player.seekToDefaultPosition(); player.prepare(); player.play(); return;
                }
                if (retries < 6) {
                    retries++;
                    ui.postDelayed(() -> reconnect(), Math.min(1000L * retries, 4000L));
                    return;
                }
                errText.setText((live ? "Cette chaîne ne passe pas pour le moment.\nEssaie une autre chaîne avec ⬆ ⬇" : "Cette vidéo ne se lance pas.\nAppuie sur OK pour réessayer, Retour pour quitter")
                        + "\n\n" + e.getErrorCodeName());
                errText.setVisibility(View.VISIBLE);
            }
        });
        player.prepare();
        player.play();
        showInfo();
        ui.postDelayed(watch, 3000);
    }

    private void showInfo() {
        if (player == null) return;
        int i = player.getCurrentMediaItemIndex();
        if (i < 0 || i >= items.size()) return;
        infoTitle.setText(items.get(i)[1]);
        String sub = items.get(i)[2];
        infoSub.setText(sub);
        infoSub.setVisibility(sub == null || sub.isEmpty() ? View.GONE : View.VISIBLE);
        info.setVisibility(View.VISIBLE);
        ui.removeCallbacks(hideInfo);
        ui.postDelayed(hideInfo, live ? 4000 : 3000);
    }

    private void zap(int d) {
        if (player == null) return;
        int n = items.size(), i = (player.getCurrentMediaItemIndex() + d + n) % n;
        player.seekToDefaultPosition(i);
        player.prepare();
        player.play();
    }

    private void seek(long ms) {
        if (player == null) return;
        long dur = player.getDuration(), p = player.getCurrentPosition() + ms;
        if (dur != C.TIME_UNSET && p > dur - 1000) p = dur - 1000;
        player.seekTo(Math.max(0, p));
        view.showController();
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        if (player == null) return super.dispatchKeyEvent(e);
        int k = e.getKeyCode();
        if (k == KeyEvent.KEYCODE_BACK) {
            if (e.getAction() == KeyEvent.ACTION_UP) {
                if (view.isControllerFullyVisible()) view.hideController();
                else finishWith(player.getCurrentMediaItemIndex(), player.getCurrentPosition(), player.getDuration());
            }
            return true;
        }
        if (e.getAction() != KeyEvent.ACTION_DOWN) return super.dispatchKeyEvent(e);
        boolean err = errText.getVisibility() == View.VISIBLE;
        if (live) {
            switch (k) {
                case KeyEvent.KEYCODE_CHANNEL_UP: zap(-1); return true;
                case KeyEvent.KEYCODE_CHANNEL_DOWN: zap(1); return true;
                case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE: case KeyEvent.KEYCODE_MEDIA_PLAY: case KeyEvent.KEYCODE_MEDIA_PAUSE:
                    if (player.isPlaying()) player.pause(); else player.play();
                    view.showController(); return true;
            }
            if (err && (k == KeyEvent.KEYCODE_DPAD_CENTER || k == KeyEvent.KEYCODE_ENTER)) { retries = 0; errText.setVisibility(View.GONE); player.prepare(); player.play(); return true; }
            if (!view.isControllerFullyVisible()) {
                // barre cachée : ⬆ ⬇ changent de chaîne, OK affiche les commandes (pause, chaîne suivante…)
                switch (k) {
                    case KeyEvent.KEYCODE_DPAD_UP: zap(-1); return true;
                    case KeyEvent.KEYCODE_DPAD_DOWN: zap(1); return true;
                    case KeyEvent.KEYCODE_DPAD_CENTER: case KeyEvent.KEYCODE_ENTER: case KeyEvent.KEYCODE_INFO: case KeyEvent.KEYCODE_MENU:
                        view.showController(); return true;
                    case KeyEvent.KEYCODE_DPAD_LEFT: case KeyEvent.KEYCODE_DPAD_RIGHT:
                        showInfo(); return true;
                }
            }
            return super.dispatchKeyEvent(e);
        }
        // films et séries
        if (err && (k == KeyEvent.KEYCODE_DPAD_CENTER || k == KeyEvent.KEYCODE_ENTER)) { retries = 0; errText.setVisibility(View.GONE); player.prepare(); player.play(); return true; }
        switch (k) {
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE: case KeyEvent.KEYCODE_MEDIA_PLAY: case KeyEvent.KEYCODE_MEDIA_PAUSE:
                if (player.isPlaying()) player.pause(); else player.play();
                view.showController(); return true;
            case KeyEvent.KEYCODE_MEDIA_FAST_FORWARD: seek(10000); return true;
            case KeyEvent.KEYCODE_MEDIA_REWIND: seek(-10000); return true;
            case KeyEvent.KEYCODE_CHANNEL_DOWN: case KeyEvent.KEYCODE_MEDIA_NEXT: if (player.hasNextMediaItem()) player.seekToNextMediaItem(); return true;
            case KeyEvent.KEYCODE_CHANNEL_UP: case KeyEvent.KEYCODE_MEDIA_PREVIOUS: if (player.hasPreviousMediaItem()) player.seekToPreviousMediaItem(); return true;
        }
        if (!view.isControllerFullyVisible()) {
            // barre cachée : ⬅ ➡ avancent ou reculent directement, OK met en pause
            switch (k) {
                case KeyEvent.KEYCODE_DPAD_LEFT: seek(-10000); return true;
                case KeyEvent.KEYCODE_DPAD_RIGHT: seek(10000); return true;
                case KeyEvent.KEYCODE_DPAD_CENTER: case KeyEvent.KEYCODE_ENTER:
                    if (player.isPlaying()) player.pause(); else player.play();
                    view.showController(); return true;
                case KeyEvent.KEYCODE_DPAD_UP: case KeyEvent.KEYCODE_DPAD_DOWN:
                    view.showController(); return true;
            }
        }
        return super.dispatchKeyEvent(e);
    }

    private void finishWith(int index, long pos, long dur) {
        if (done) return;
        done = true;
        try {
            JSONObject r = new JSONObject();
            r.put("index", index); r.put("pos", Math.max(0, pos)); r.put("dur", dur == C.TIME_UNSET ? 0 : Math.max(0, dur));
            setResult(RESULT_OK, new Intent().putExtra("result", r.toString()));
        } catch (Exception e) { setResult(RESULT_OK); }
        release();
        finish();
    }

    private void release() {
        ui.removeCallbacks(watch);
        ui.removeCallbacksAndMessages(null);
        if (player != null) { try { player.release(); } catch (Exception e) { } player = null; }
    }

    @Override
    protected void onStop() {
        super.onStop();
        if (!done && player != null && !isChangingConfigurations()) finishWith(player.getCurrentMediaItemIndex(), player.getCurrentPosition(), player.getDuration());
    }

    @Override
    protected void onDestroy() { release(); super.onDestroy(); }
}
