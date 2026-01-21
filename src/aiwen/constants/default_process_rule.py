DEFAULT_PROCESS_RULE = {
    "data_source": {
        "type": "upload_file",
        "info_list": {
            "data_source_type": "upload_file",
            "file_info_list": {
                "file_ids": []  # 这里会在运行时填入实际的file_id
            }
        }
    },
    "indexing_technique": "economy",
    "process_rule": {
        "rules": {
            "pre_processing_rules": [
                {
                    "id": "remove_extra_spaces",
                    "enabled": True
                },
                {
                    "id": "remove_urls_emails",
                    "enabled": False
                }
            ],
            "segmentation": {
                "separator": "\\n\\n",  # 修改为双换行符
                "max_tokens": 1024,     # 修改为1024
                "chunk_overlap": 50     # 添加chunk_overlap
            }
        },
        "mode": "custom"  # 修改为custom模式
    },
    "doc_form": "text_model",
    "doc_language": "Chinese Simplified",
    "retrieval_model": {
        "search_method": "semantic_search",
        "reranking_enable": False,
        "reranking_mode": None,
        "reranking_model": {
            "reranking_provider_name": "",
            "reranking_model_name": ""
        },
        "weights": None,
        "top_k": 2,
        "score_threshold_enabled": False,
        "score_threshold": 0
    },
    "embedding_model": "",
    "embedding_model_provider": ""
}
