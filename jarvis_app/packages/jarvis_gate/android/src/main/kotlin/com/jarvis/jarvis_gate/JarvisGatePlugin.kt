package com.jarvis.jarvis_gate

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.util.Log
import androidx.car.app.connection.CarConnection
import androidx.lifecycle.Observer
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/** Charging + Android Auto state. Registered per engine, so the foreground-task isolate gets it too. */
class JarvisGatePlugin : FlutterPlugin, MethodChannel.MethodCallHandler, EventChannel.StreamHandler {
    private lateinit var context: Context
    private lateinit var methods: MethodChannel
    private lateinit var events: EventChannel
    private val carType by lazy { CarConnection(context).type }

    private var sink: EventChannel.EventSink? = null
    private var charging = false
    private var androidAuto = false

    private val powerReceiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) {
            charging = intent.action == Intent.ACTION_POWER_CONNECTED
            emit()
        }
    }
    private val carObserver = Observer<Int> { type ->
        androidAuto = type == CarConnection.CONNECTION_TYPE_PROJECTION
        emit()
    }

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        context = binding.applicationContext
        methods = MethodChannel(binding.binaryMessenger, "jarvis_gate").also { it.setMethodCallHandler(this) }
        events = EventChannel(binding.binaryMessenger, "jarvis_gate/changes").also { it.setStreamHandler(this) }
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        methods.setMethodCallHandler(null)
        events.setStreamHandler(null)
        onCancel(null)
    }

    // Platform channel calls arrive on the main thread, which LiveData.observeForever requires.
    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        if (call.method != "state") return result.notImplemented()
        // CarConnection's LiveData only learns the type once observed: observe until the first value.
        val once = object : Observer<Int> {
            override fun onChanged(value: Int) {
                carType.removeObserver(this)
                result.success(map(stickyCharging(), value == CarConnection.CONNECTION_TYPE_PROJECTION))
            }
        }
        carType.observeForever(once)
    }

    override fun onListen(arguments: Any?, events: EventChannel.EventSink) {
        if (sink != null) onCancel(null)
        sink = events
        charging = stickyCharging()
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_POWER_CONNECTED)
            addAction(Intent.ACTION_POWER_DISCONNECTED)
        }
        if (Build.VERSION.SDK_INT >= 33) {
            context.registerReceiver(powerReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            context.registerReceiver(powerReceiver, filter)
        }
        carType.observeForever(carObserver) // emits the current state on first value
    }

    override fun onCancel(arguments: Any?) {
        if (sink == null) return
        sink = null
        context.unregisterReceiver(powerReceiver)
        carType.removeObserver(carObserver)
    }

    private fun emit() {
        Log.i("JarvisGate", "change charging=$charging androidAuto=$androidAuto")
        sink?.success(map(charging, androidAuto))
    }

    private fun stickyCharging(): Boolean {
        val battery = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        return (battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
    }

    private fun map(charging: Boolean, androidAuto: Boolean) =
        mapOf("charging" to charging, "androidAuto" to androidAuto)
}
