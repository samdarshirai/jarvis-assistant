package com.jarvis.jarvis_app

import android.app.AlarmManager
import android.content.Context
import android.content.Intent
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
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
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/audio").setMethodCallHandler { call, result ->
            when (call.method) {
                "headsetStart" -> result.success(headsetStart())
                "headsetStop" -> { headsetStop(); result.success(null) }
                else -> result.notImplemented()
            }
        }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/alarm").setMethodCallHandler { call, result ->
            if (call.method == "next") {
                val am = getSystemService(Context.ALARM_SERVICE) as AlarmManager
                result.success(am.nextAlarmClock?.triggerTime)
            } else result.notImplemented()
        }
    }

    private val am get() = getSystemService(Context.AUDIO_SERVICE) as AudioManager

    /** Routes call-style audio (mic and playback) through a connected headset; false when there is none. */
    private fun headsetStart(): Boolean {
        val types = setOf(
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_BLE_HEADSET,
            AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_USB_HEADSET,
        )
        am.mode = AudioManager.MODE_IN_COMMUNICATION
        val ok = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val d = am.availableCommunicationDevices.firstOrNull { it.type in types }
            d != null && am.setCommunicationDevice(d)
        } else {
            @Suppress("DEPRECATION")
            if (am.isBluetoothScoAvailableOffCall) { am.startBluetoothSco(); am.isBluetoothScoOn = true; true } else false
        }
        if (!ok) am.mode = AudioManager.MODE_NORMAL
        return ok
    }

    private fun headsetStop() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) am.clearCommunicationDevice()
        else @Suppress("DEPRECATION") { am.stopBluetoothSco(); am.isBluetoothScoOn = false }
        am.mode = AudioManager.MODE_NORMAL
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
