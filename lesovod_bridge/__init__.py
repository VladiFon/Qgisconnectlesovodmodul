def classFactory(iface):
    from .lesovod_bridge_plugin import LesovodBridgePlugin
    return LesovodBridgePlugin(iface)
