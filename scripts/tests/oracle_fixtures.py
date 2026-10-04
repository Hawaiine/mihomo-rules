"""Hand-authored, independent oracle fixtures; neither side is derived at runtime."""

EXPECTED = {
    "brands": ["Apple", "ApplePodcasts"],
    "parents": {"ApplePodcasts": "Apple"},
    "providers": ["Apple", "ApplePodcasts"],
    "groups": ["Apple", "Apple Podcasts"],
    "rules": ["RULE-SET,ApplePodcasts,Apple", "RULE-SET,Apple,Apple"],
    "icons": ["https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/f0f3bc2a44616885682ee5f0e5921540b964e2d8/icons/Apple/ApplePodcasts/ApplePodcasts.png"],
    "overrides": [],
    "preserved": {"proxy-providers": "user-local"},
    "allowed_generated_keys": ["rules", "rule-providers", "proxy-groups"],
    "platforms": {
        "android": {
            "port": 7891,
            "socks-port": 7892,
            "keep-alive-idle": 15,
            "find-process-mode": "strict",
            "external-controller": "127.0.0.1:9090",
            "dns_listen": "127.0.0.1:53",
            "tun_enabled": False,
            "applications_rule": "present",
        },
        "nikki": {
            "port": 8080,
            "socks-port": 1080,
            "keep-alive-idle": 600,
            "find-process-mode": "off",
            "external-controller": "0.0.0.0:9090",
            "dns_listen": "0.0.0.0:1053",
            "tun_enabled": True,
            "applications_rule": "absent",
        },
    },
    "full": {
        "rules": ["RULE-SET,ApplePodcasts,Apple", "RULE-SET,Apple,Apple"],
        "rule-providers": {"Apple": {}, "ApplePodcasts": {}},
        "proxy-groups": [{"name": "Apple", "type": "select", "icon": "expected-full-icon"}],
    },
    "min": {
        "rules": ["RULE-SET,ApplePodcasts,Apple", "RULE-SET,Apple,Apple"],
        "rule-providers": {"Apple": {}, "ApplePodcasts": {}},
        "proxy-groups": [{"name": "Apple", "type": "select", "icon": "expected-min-icon"}],
    },
}

ACTUAL = {
    "brands": ["Apple", "ApplePodcasts"],
    "parents": {"ApplePodcasts": "Apple"},
    "providers": ["Apple", "ApplePodcasts"],
    "groups": ["Apple", "Apple Podcasts"],
    "rules": ["RULE-SET,ApplePodcasts,Apple", "RULE-SET,Apple,Apple"],
    "icons": ["https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/f0f3bc2a44616885682ee5f0e5921540b964e2d8/icons/Apple/ApplePodcasts/ApplePodcasts.png"],
    "overrides": [],
    "preserved": {"proxy-providers": "user-local"},
    "generated_keys": ["rules", "rule-providers", "proxy-groups"],
    "platforms": {
        "android": {
            "port": 7891,
            "socks-port": 7892,
            "keep-alive-idle": 15,
            "find-process-mode": "strict",
            "external-controller": "127.0.0.1:9090",
            "dns_listen": "127.0.0.1:53",
            "tun_enabled": False,
            "rules": ["RULE-SET,Applications,🎯 全球直连"],
        },
        "nikki": {
            "port": 8080,
            "socks-port": 1080,
            "keep-alive-idle": 600,
            "find-process-mode": "off",
            "external-controller": "0.0.0.0:9090",
            "dns_listen": "0.0.0.0:1053",
            "tun_enabled": True,
            "rules": [],
        },
    },
    "full": {
        "rules": ["RULE-SET,ApplePodcasts,Apple", "RULE-SET,Apple,Apple"],
        "rule-providers": {"Apple": {}, "ApplePodcasts": {}},
        "proxy-groups": [{"name": "Apple", "type": "select", "icon": "actual-full-icon"}],
    },
    "min": {
        "rules": ["RULE-SET,ApplePodcasts,Apple", "RULE-SET,Apple,Apple"],
        "rule-providers": {"Apple": {}, "ApplePodcasts": {}},
        "proxy-groups": [{"name": "Apple", "type": "select", "icon": "actual-min-icon"}],
    },
}
