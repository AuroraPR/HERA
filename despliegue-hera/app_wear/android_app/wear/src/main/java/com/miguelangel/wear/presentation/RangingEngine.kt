package com.miguelangel.wear.presentation

interface RangingEngine {
    suspend fun prepare(): Boolean
    suspend fun start(anchor: String): Boolean
    fun stop()
    fun getLocalAddress(): String?
    fun getLastDistance(): Float?
}