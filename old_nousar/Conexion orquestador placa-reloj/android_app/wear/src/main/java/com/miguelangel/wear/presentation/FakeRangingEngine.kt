package com.miguelangel.wear.presentation

class FakeRangingEngine : RangingEngine {

    private var localAddress: String = "FA:KE"
    private var lastDistance: Float? = null
    private var running = false

    override suspend fun prepare(): Boolean {
        return true
    }

    override suspend fun start(anchor: String): Boolean {
        running = true
        lastDistance = when (anchor) {
            "00:01" -> 1.25f
            "00:02" -> 2.10f
            "00:03" -> 3.40f
            "00:04" -> 4.05f
            else -> 2.50f
        }
        return true
    }

    override fun stop() {
        running = false
        lastDistance = null
    }

    override fun getLocalAddress(): String? = localAddress

    override fun getLastDistance(): Float? = if (running) lastDistance else null
}