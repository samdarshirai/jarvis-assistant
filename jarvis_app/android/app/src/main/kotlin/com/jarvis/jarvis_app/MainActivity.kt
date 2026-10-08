package com.jarvis.jarvis_app

import android.app.AlarmManager
import android.content.Context
import android.content.Intent
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var channel: MethodChannel? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/launch").also { ch ->
            ch.setMethodCallHandler { call, result ->
                if (call.method == "launchedByAssistant") {
                    val started = intent?.getBooleanExtra(EXTRA_START, false) == true
                    intent?.removeExtra(EXTRA_START)
                    result.success(started)
                } else result.notImplemented()
            }
        }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/alarm").setMethodCallHandler { call, result ->
            if (call.method == "next") {
                val am = getSystemService(Context.ALARM_SERVICE) as AlarmManager
                result.success(am.nextAlarmClock?.triggerTime)
            } else result.notImplemented()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent.getBooleanExtra(EXTRA_START, false)) {
            intent.removeExtra(EXTRA_START)
            channel?.invokeMethod("start", null)
        }
    }

    companion object { const val EXTRA_START = "jarvis_start" }
}
