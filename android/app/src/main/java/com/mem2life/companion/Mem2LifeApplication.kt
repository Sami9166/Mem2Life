package com.mem2life.companion

import android.app.Application
import android.util.Log
import com.meta.wearable.dat.core.Wearables

private const val TAG = "Mem2Life:Application"

class Mem2LifeApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        Wearables.initialize(this).onFailure { error, _ ->
            Log.e(TAG, "DAT SDK 초기화 실패: ${error.description}")
        }
    }
}
