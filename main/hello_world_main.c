#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "string.h"
#include "esp_mac.h"
#include "rom/ets_sys.h"
#include "esp_now.h"
#include "esp_idf_version.h"

#define WIFI_SSID     CONFIG_ESP_WIFI_SSID
#define WIFI_PASS     CONFIG_ESP_WIFI_PASSWORD

static const char *TAG = "CSI";
static EventGroupHandle_t wifi_event_group;
const int CONNECTED_BIT = BIT0;
static uint8_t ap_bssid[6] = {0};  // Global variable to store AP's BSSID
static uint8_t sta_mac[6] = {0};

#define ESPNOW_PING_MAGIC "ESP32_CSI_PING"

typedef struct {
    char magic[16];
    uint32_t seq;
} __attribute__((packed)) espnow_ping_payload_t;

static const uint8_t s_broadcast_mac[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
static uint8_t peer_mac[6] = {0};
static volatile bool peer_discovered = false;
static uint32_t ping_seq = 0;


wifi_ap_record_t ap_info  = {0};


// Wi-Fi event handler
static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_CONNECTED) {
         wifi_event_sta_connected_t *event = (wifi_event_sta_connected_t *)event_data;
        
        // Store the BSSID of the connected AP
        memcpy(ap_bssid, event->bssid, sizeof(ap_bssid));

        ESP_LOGI(TAG, "Connected to AP");
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(wifi_event_group, CONNECTED_BIT);
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        esp_wifi_connect();
        xEventGroupClearBits(wifi_event_group, CONNECTED_BIT);
    }
}
#define PERIOD_MS 100
// CSI data callback
static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf) {
        ESP_LOGW(TAG, "<%s> wifi_csi_cb", esp_err_to_name(ESP_ERR_INVALID_ARG));
        return;
    }

    static const uint8_t null_mac[6] = {0};
    bool is_ap_ack = (memcmp(info->mac, null_mac, 6) == 0 && memcmp(info->dmac, sta_mac, 6) == 0);
    bool is_peer_frame = (peer_discovered && memcmp(info->mac, peer_mac, 6) == 0);

    // Filter CSI: dump AP ACK frames or frames from discovered peer MAC
    if (!is_ap_ack && !is_peer_frame) {
        return;
    }

    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;
    const uint8_t *dest_mac_to_print = is_peer_frame ? info->mac : ap_info.bssid;

    ets_printf("CSI_DATA,%u,%d,"MACSTR","MACSTR",%d,%d,%d,%d,%u,%u,%u,%u",
        0, rx_ctrl->rx_channel_estimate_info_vld, MAC2STR(info->mac), MAC2STR(dest_mac_to_print), rx_ctrl->rssi, rx_ctrl->rate,
        rx_ctrl->noise_floor, rx_ctrl->channel, 0,
        rx_ctrl->timestamp, rx_ctrl->sig_len, rx_ctrl->rx_state);
    ets_printf(",%d,%u,\"[%d", info->len, 0, info->buf[0]);
    for (int i = 1; i < info->len; i++) {
        ets_printf(",%d",  info->buf[i]);
    }
    ets_printf("]\"\n");
}

// Initialize CSI collection
void wifi_init_csi() {
  /**< default config */
    wifi_csi_config_t  csi_config = {
        .enable = true,                    /**< enable to acquire CSI */
        .acquire_csi_legacy = true,        /**< enable to acquire L-LTF when receiving a 11g PPDU */
#if CONFIG_SOC_WIFI_MAC_VERSION_NUM == 3
        .acquire_csi_force_lltf = true,
#endif
        .acquire_csi_ht20 = true,          /**< enable to acquire HT-LTF when receiving an HT20 PPDU */
        .acquire_csi_ht40 = false,         /**< enable to acquire HT-LTF when receiving an HT40 PPDU */
        .acquire_csi_su = false,            /**< enable to acquire HE-LTF when receiving an HE20 SU PPDU */
        .acquire_csi_mu = false,           /**< enable to acquire HE-LTF when receiving an HE20 MU PPDU */
        .acquire_csi_dcm = false,           /**< enable to acquire HE-LTF when receiving an HE20 DCM applied PPDU */
        .acquire_csi_beamformed = false,    /**< enable to acquire HE-LTF when receiving an HE20 Beamformed applied PPDU */
        .val_scale_cfg = 3,             /**< value 0-3 */
        .dump_ack_en = true,               /**< enable to dump 802.11 ACK frame, default disabled */
    };
    ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_STA, sta_mac));
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

// ESP-NOW receive callback
static void espnow_recv_cb(const esp_now_recv_info_t *recv_info, const uint8_t *data, int data_len)
{
    if (!recv_info || !data) {
        return;
    }
    const uint8_t *src_mac = recv_info->src_addr;


    if (data_len < (int)sizeof(espnow_ping_payload_t)) {
        return;
    }

    const espnow_ping_payload_t *payload = (const espnow_ping_payload_t *)data;
    if (memcmp(payload->magic, ESPNOW_PING_MAGIC, strlen(ESPNOW_PING_MAGIC)) != 0) {
        return;
    }

    // Ignore frames from self
    if (memcmp(src_mac, sta_mac, 6) == 0) {
        return;
    }

    // If another node running the same software sees this payload, start dumping CSI from that MAC
    if (!peer_discovered || memcmp(peer_mac, src_mac, 6) != 0) {
        memcpy(peer_mac, src_mac, 6);
        peer_discovered = true;
        ESP_LOGI(TAG, "Discovered peer MAC: " MACSTR " with unique ESP-NOW payload, now dumping CSI", MAC2STR(peer_mac));
    }
}

// Initialize ESP-NOW with broadcast peer and MCS0 transmission rate
void wifi_init_espnow(void) {
    ESP_ERROR_CHECK(esp_now_init());

    


    ESP_ERROR_CHECK(esp_now_register_recv_cb(espnow_recv_cb));

    // Register broadcast peer
    esp_now_peer_info_t peer;
    memset(&peer, 0, sizeof(peer));
    memcpy(peer.peer_addr, s_broadcast_mac, 6);
    peer.channel = 0;
    peer.ifidx = WIFI_IF_STA;
    peer.encrypt = false;
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));


    esp_now_rate_config_t rate_config = {
        .phymode = WIFI_PHY_MODE_HT20,
        .ersu = false,
        .dcm = false,
        .rate = WIFI_PHY_RATE_MCS0_LGI,
    };
    uint8_t broadcast_mac[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(broadcast_mac, &rate_config));


    ESP_LOGI(TAG, "ESP-NOW broadcast peer initialized at MCS0 rate");
}

// Function to send ESP-NOW broadcast frame with unique payload
void send_espnow_ping(void) {
    espnow_ping_payload_t payload;
    memset(&payload, 0, sizeof(payload));
    strncpy(payload.magic, ESPNOW_PING_MAGIC, sizeof(payload.magic) - 1);
    payload.seq = ping_seq++;

    esp_err_t err = esp_now_send(s_broadcast_mac, (const uint8_t *)&payload, sizeof(payload));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to send ESP-NOW broadcast: %s", esp_err_to_name(err));
    }
}

// Function to transmit a raw Wi-Fi frame
void send_raw_frame() {

    esp_wifi_sta_get_ap_info(&ap_info);

   typedef struct {
    uint8_t frame_control[2];
    uint16_t duration;
    uint8_t destination_address[6];
    uint8_t source_address[6];
    uint8_t broadcast_address[6];
    uint16_t sequence_control;
} __attribute__((packed)) wifi_null_data_t;

    wifi_null_data_t null_data = {
        .frame_control       = {0x48, 0x01},
        .duration            = 0x0000,
        .sequence_control    = 0x0000,
    };

    memcpy(null_data.destination_address, ap_info.bssid, 6);
    memcpy(null_data.broadcast_address, ap_info.bssid, 6);
    memcpy(null_data.source_address, sta_mac, 6);

    // Transmit the raw frame
    esp_err_t err = esp_wifi_80211_tx(WIFI_IF_STA,  &null_data, sizeof(null_data), true);
    if (err == ESP_OK) {
        //ESP_LOGI(TAG, "Raw frame sent successfully");
    } else {
        ESP_LOGE(TAG, "Failed to send raw frame: %d", err);
    }
}

// Wi-Fi initialization in station mode
void wifi_init_sta() {
    wifi_event_group = xEventGroupCreate();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);

    ESP_ERROR_CHECK(esp_event_loop_create_default()); 
    ESP_ERROR_CHECK(esp_netif_init());
    esp_netif_create_default_wifi_sta();

    esp_event_handler_instance_t instance_any_id;
    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                        &wifi_event_handler, NULL, &instance_any_id);

    esp_event_handler_instance_register(IP_EVENT, ESP_EVENT_ANY_ID,
                                        &wifi_event_handler, NULL, &instance_any_id);

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
        },
    };
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    esp_wifi_start();
    
    xEventGroupWaitBits(wifi_event_group, CONNECTED_BIT, false, true, portMAX_DELAY);
    ESP_LOGI(TAG, "wifi_init_sta finished.");
    wifi_init_csi();
    wifi_init_espnow();
}

// Task to send raw frames and ESP-NOW broadcast frames periodically
void raw_frame_task(void *pvParameters) {
    wifi_tx_rate_config_t config = {.phymode = WIFI_PHY_MODE_HT20, .rate = WIFI_PHY_RATE_MCS0_LGI};
    ESP_ERROR_CHECK(esp_wifi_config_80211_tx(WIFI_IF_STA, &config));
    while (1) {
        send_raw_frame();
        send_espnow_ping();
        vTaskDelay(pdMS_TO_TICKS(PERIOD_MS));
    }
}

void app_main() {
    // Initialize NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init_sta();

    // Create task to send raw frames periodically
    xTaskCreate(raw_frame_task, "raw_frame_task", 4096, NULL, 5, NULL);
}
