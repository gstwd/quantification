/** 统一数据管理接口封装。 */

import { apiClient } from './client'
import type {
  DataManagementOperationAccepted,
  DataManagementOperationRequest,
  DataManagementOverview,
  DataSetDetailResponse,
} from '../types/api'

/** 获取全部数据集的当前健康总览。 */
export async function fetchDataManagementOverview(): Promise<DataManagementOverview> {
  const { data } = await apiClient.get<DataManagementOverview>('/data-management')
  return data
}

/** 获取一个数据集的分页分区健康详情。 */
export async function fetchDataSetDetail(
  datasetKey: string,
  offset = 0,
  limit = 50,
  partitionKey?: string,
): Promise<DataSetDetailResponse> {
  const { data } = await apiClient.get<DataSetDetailResponse>(`/data-management/datasets/${datasetKey}`, {
    params: { offset, limit, partition_key: partitionKey },
  })
  return data
}

/** 提交全局、数据集或单对象的数据维护操作。 */
export async function triggerDataManagementOperation(
  request: DataManagementOperationRequest,
): Promise<DataManagementOperationAccepted> {
  const { data } = await apiClient.post<DataManagementOperationAccepted>('/data-management/operations', request)
  return data
}
